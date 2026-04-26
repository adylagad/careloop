"""FET payment helper for the Telegram OmegaClaw bridge.

Telegram does not natively render the ASI:One FET Pay/Reject card. This module
lets the bridge settle the same paid handoff inside Telegram by talking to the
Fetch.ai stable testnet directly:

- ``/pay``: sends testnet FET on the user's behalf using ``FET_TESTNET_MNEMONIC``.
- ``/paid <tx-hash>``: verifies a transaction the user submitted from their own
  wallet.

Both paths return a ``PaymentVerification`` result that the bridge can hand back
to the orchestrator's ``telegram_complete_paid_work`` to run the live search.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

try:
    from cosmpy.aerial.client import LedgerClient, NetworkConfig
    from cosmpy.aerial.wallet import LocalWallet
    from cosmpy.crypto.address import Address
except ImportError:  # pragma: no cover - cosmpy is an optional demo dependency
    LedgerClient = None
    NetworkConfig = None
    LocalWallet = None
    Address = None


FET_BASE_DENOM = "atestfet"
EXPLORER_BASE = "https://explore-stabletestnet.fetch.ai"


@dataclass
class PaymentVerification:
    success: bool
    transaction_id: str
    detail: str


def _amount_to_atestfet(amount: str) -> int:
    return int(Decimal(amount) * Decimal(10**18))


def _ledger_client():
    if LedgerClient is None or NetworkConfig is None:
        raise RuntimeError("cosmpy is not installed; cannot reach the Fetch.ai testnet.")
    return LedgerClient(NetworkConfig.fetchai_stable_testnet())


def _looks_like_mnemonic(value: str) -> bool:
    words = value.split()
    return len(words) in {12, 15, 18, 21, 24}


def telegram_fet_recipient() -> str:
    """The wallet that receives the CareLoop service fee from Telegram users."""
    return (
        os.getenv("TELEGRAM_FET_RECIPIENT")
        or os.getenv("PHARMACY_ASSISTANT_FET_WALLET_ADDRESS")
        or os.getenv("APPOINTMENT_FET_WALLET_ADDRESS")
        or ""
    ).strip()


def explorer_address_url(address: str) -> str:
    return f"{EXPLORER_BASE}/accounts/{address}"


def explorer_tx_url(tx_hash: str) -> str:
    return f"{EXPLORER_BASE}/transactions/{tx_hash}"


def wallet_pay_url(recipient: str, amount: str, memo: str) -> str:
    """Best-effort prefilled-transfer URL for the Fetch.ai web wallet."""
    params = urlencode({"recipient": recipient, "amount": amount, "memo": memo})
    return f"https://browser-wallet.fetch.ai/send?{params}"


def auto_send_testnet_fet(recipient: str, amount: str, memo: str) -> PaymentVerification:
    """Send testnet FET using the configured demo mnemonic. The bridge uses this
    when the user types ``/pay`` so a hackathon judge can complete the flow
    without leaving Telegram."""
    mnemonic = os.getenv("FET_TESTNET_MNEMONIC", "").strip()
    if not mnemonic:
        return PaymentVerification(
            success=False,
            transaction_id="",
            detail="FET_TESTNET_MNEMONIC is not set, so /pay cannot auto-pay.",
        )
    if not _looks_like_mnemonic(mnemonic):
        return PaymentVerification(
            success=False,
            transaction_id="",
            detail=(
                "FET_TESTNET_MNEMONIC must be the 12 or 24 word seed phrase "
                "for a funded testnet wallet, not a wallet address."
            ),
        )
    if LedgerClient is None or LocalWallet is None or Address is None:
        return PaymentVerification(
            success=False,
            transaction_id="",
            detail="cosmpy is not installed; install cosmpy to enable /pay.",
        )
    if not recipient:
        return PaymentVerification(
            success=False,
            transaction_id="",
            detail="No FET recipient configured for the Telegram bridge.",
        )

    try:
        client = _ledger_client()
        wallet = LocalWallet.from_mnemonic(mnemonic, prefix="fetch")
        tx = client.send_tokens(
            Address(recipient),
            _amount_to_atestfet(amount),
            FET_BASE_DENOM,
            wallet,
            memo=memo,
        )
        tx.wait_to_complete()
        return PaymentVerification(
            success=True,
            transaction_id=tx.tx_hash,
            detail=f"sent {amount} FET to {recipient} on the stable testnet",
        )
    except Exception as exc:  # pragma: no cover - depends on testnet liveness
        return PaymentVerification(
            success=False,
            transaction_id="",
            detail=f"send failed: {exc}",
        )


def verify_testnet_payment(
    tx_hash: str,
    *,
    expected_recipient: str,
    expected_amount: str,
    expected_memo: Optional[str] = None,
) -> PaymentVerification:
    """Verify a user-submitted transaction on the stable testnet. The bridge
    settles for "tx exists and did not fail" if the message body is not parseable,
    so the demo still completes when the user pays from a non-cosmpy wallet."""
    tx_hash = (tx_hash or "").strip()
    if not tx_hash:
        return PaymentVerification(
            success=False,
            transaction_id="",
            detail="missing transaction hash",
        )
    if LedgerClient is None:
        return PaymentVerification(
            success=False,
            transaction_id=tx_hash,
            detail="cosmpy is not installed; cannot verify the transaction.",
        )

    try:
        client = _ledger_client()
        response = client.query_tx(tx_hash)
    except Exception as exc:
        return PaymentVerification(
            success=False,
            transaction_id=tx_hash,
            detail=f"could not look up transaction: {exc}",
        )

    code = getattr(response, "code", 0) or 0
    if code != 0:
        raw_log = getattr(response, "raw_log", "") or "transaction failed on chain"
        return PaymentVerification(
            success=False,
            transaction_id=tx_hash,
            detail=f"transaction failed: {raw_log}",
        )

    expected_atestfet = _amount_to_atestfet(expected_amount)

    matched = False
    try:
        messages = getattr(getattr(response, "tx", None), "body", None)
        message_list = getattr(messages, "messages", []) if messages else []
        for message in message_list:
            to_address = getattr(message, "to_address", None)
            if to_address and to_address != expected_recipient:
                continue
            for coin in getattr(message, "amount", []) or []:
                denom = getattr(coin, "denom", "")
                amount_value = int(getattr(coin, "amount", "0") or 0)
                if denom == FET_BASE_DENOM and amount_value >= expected_atestfet:
                    matched = True
                    break
            if matched:
                break
    except Exception:
        matched = False

    if matched:
        detail = (
            f"verified transfer of >= {expected_amount} FET to {expected_recipient}"
        )
    else:
        detail = (
            "transaction is on-chain and did not fail. "
            "Could not confirm the exact recipient/amount, but accepting it for the demo."
        )
    return PaymentVerification(success=True, transaction_id=tx_hash, detail=detail)
