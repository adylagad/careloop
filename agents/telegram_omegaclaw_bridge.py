"""Telegram bridge for the CareLoop OmegaClaw demo.

This bridge is intentionally small: Telegram is the user-facing channel, while
CareLoop Orchestrator keeps the same routing/state behavior used in ASI:One.

Telegram does not natively render the ASI:One FET Pay/Reject card, so the
bridge also drives FET payment directly inside Telegram. When the orchestrator
hands off to a paid route (appointment search, OTC pharmacy search), the bridge
attaches a Pay card with the recipient wallet, amount, and memo. Users complete
the payment with ``/pay`` (auto from the demo wallet) or ``/paid <tx-hash>``
(manual transfer + verification on the Fetch.ai stable testnet).
"""
from __future__ import annotations

import os
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

from models import CareRequest, PaymentQuote
from orchestrator_agent import (
    orchestrator_chat_response,
    telegram_complete_paid_work,
    telegram_pending_paid_quote,
)
from telegram_fet_payment import (
    auto_send_testnet_fet,
    explorer_address_url,
    explorer_tx_url,
    telegram_fet_recipient,
    verify_testnet_payment,
)


load_dotenv()
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_POLL_SECONDS = 1.5
MAX_TELEGRAM_MESSAGE_LENGTH = 3900
PAID_HANDOFF_MARKER = "FET CareLoop service fee"


@dataclass
class TelegramConfig:
    token: str
    allowed_chat_ids: set[str]
    poll_seconds: float


@dataclass
class PendingTelegramPayment:
    route: str
    request: CareRequest
    quote: PaymentQuote
    recipient: str
    created_at: float = field(default_factory=time.time)


PENDING_PAYMENTS: dict[int, PendingTelegramPayment] = {}


def load_config() -> TelegramConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN. Create a bot with BotFather and put the token in .env.")

    allowed_raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    allowed_chat_ids = {item.strip() for item in allowed_raw.split(",") if item.strip()}
    poll_seconds = float(os.getenv("TELEGRAM_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))
    return TelegramConfig(token=token, allowed_chat_ids=allowed_chat_ids, poll_seconds=poll_seconds)


def telegram_sender_id(chat_id: int | str) -> str:
    return f"telegram:{chat_id}"


def split_telegram_message(text: str) -> list[str]:
    if len(text) <= MAX_TELEGRAM_MESSAGE_LENGTH:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        chunk = remaining[:MAX_TELEGRAM_MESSAGE_LENGTH]
        split_at = max(chunk.rfind("\n\n"), chunk.rfind("\n"), chunk.rfind(" "))
        if split_at < 1200:
            split_at = len(chunk)
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def telegram_api_url(token: str, method: str) -> str:
    return TELEGRAM_API_BASE.format(token=token, method=method)


def is_allowed_chat(config: TelegramConfig, chat_id: int | str) -> bool:
    return not config.allowed_chat_ids or str(chat_id) in config.allowed_chat_ids


def send_message(config: TelegramConfig, chat_id: int | str, text: str) -> None:
    for chunk in split_telegram_message(text):
        response = httpx.post(
            telegram_api_url(config.token, "sendMessage"),
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        response.raise_for_status()


def _update_text(update: dict[str, Any]) -> tuple[int | None, str | None]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    return chat_id, text


def _format_pay_card(pending: PendingTelegramPayment) -> str:
    return (
        "FET payment inside Telegram\n\n"
        f"Amount: {pending.quote.amount} FET (Fetch.ai stable testnet)\n"
        f"Recipient: {pending.recipient}\n"
        f"Memo / reference: {pending.quote.reference}\n\n"
        "Two ways to pay:\n"
        "1. Reply /pay to have the demo wallet send the testnet FET for you.\n"
        "2. Open your Fetch.ai wallet on the stable testnet, send the amount above to "
        "the recipient with that exact memo, then reply /paid <tx-hash> to verify.\n\n"
        f"Recipient explorer: {explorer_address_url(pending.recipient)}"
    )


def _attach_pay_card(chat_id: int, sender: str, response: str) -> str:
    if PAID_HANDOFF_MARKER not in response:
        return response

    quote_info = telegram_pending_paid_quote(sender)
    if quote_info is None:
        return response
    route, request, quote = quote_info

    existing = PENDING_PAYMENTS.get(chat_id)
    if (
        existing
        and existing.route == route
        and existing.request.text == request.text
    ):
        pending = existing
    else:
        recipient = telegram_fet_recipient()
        if not recipient:
            return (
                f"{response}\n\n"
                "FET payment inside Telegram is not configured. "
                "Set TELEGRAM_FET_RECIPIENT (or PHARMACY_ASSISTANT_FET_WALLET_ADDRESS) in .env "
                "to enable /pay and /paid for this demo."
            )
        pending = PendingTelegramPayment(
            route=route,
            request=request,
            quote=quote,
            recipient=recipient,
        )
        PENDING_PAYMENTS[chat_id] = pending

    return f"{response}\n\n{_format_pay_card(pending)}"


def _finalize_payment(chat_id: int, sender: str, pending: PendingTelegramPayment, tx_hash: str, detail: str) -> str:
    summary = telegram_complete_paid_work(
        sender,
        pending.route,
        pending.request,
        pending.quote,
        tx_hash,
    )
    PENDING_PAYMENTS.pop(chat_id, None)
    explorer = f"\nExplorer: {explorer_tx_url(tx_hash)}" if tx_hash else ""
    header = (
        f"FET payment confirmed ({detail}).\n"
        f"Tx: {tx_hash or 'demo-tx'}{explorer}\n\n"
        "Running the CareLoop live search now...\n\n"
    )
    return header + summary


def _handle_pay_command(chat_id: int, sender: str, args: str) -> str:
    args = args.strip()
    if args:
        return _handle_paid_command(chat_id, sender, args)
    pending = PENDING_PAYMENTS.get(chat_id)
    if pending is None:
        return (
            "There is no FET payment waiting in this chat. Ask CareLoop for an "
            "appointment or OTC pharmacy search first, then reply /pay."
        )
    result = auto_send_testnet_fet(pending.recipient, pending.quote.amount, pending.quote.reference)
    if not result.success:
        return (
            f"FET auto-payment failed: {result.detail}\n\n"
            "You can still pay manually and reply /paid <tx-hash>, "
            f"sending {pending.quote.amount} FET to {pending.recipient} "
            f"with memo {pending.quote.reference}."
        )
    return _finalize_payment(chat_id, sender, pending, result.transaction_id, result.detail)


def _handle_paid_command(chat_id: int, sender: str, args: str) -> str:
    pending = PENDING_PAYMENTS.get(chat_id)
    if pending is None:
        return "There is no FET payment waiting in this chat."
    tx_hash = args.strip()
    if not tx_hash:
        return (
            "Reply with the transaction hash, like `/paid 0xABC123...`, after "
            f"sending {pending.quote.amount} FET to {pending.recipient} on the stable testnet."
        )
    result = verify_testnet_payment(
        tx_hash,
        expected_recipient=pending.recipient,
        expected_amount=pending.quote.amount,
        expected_memo=pending.quote.reference,
    )
    if not result.success:
        return (
            f"Could not verify {tx_hash}: {result.detail}\n\n"
            "Double-check the hash on the stable-testnet explorer and try /paid again."
        )
    return _finalize_payment(chat_id, sender, pending, result.transaction_id, result.detail)


def _handle_payment_status(chat_id: int) -> str:
    pending = PENDING_PAYMENTS.get(chat_id)
    if pending is None:
        return "No FET payment is currently waiting in this chat."
    return _format_pay_card(pending)


def _split_command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return command, args


def handle_text(config: TelegramConfig, chat_id: int | str, text: str) -> str:
    normalized = text.strip()
    if normalized == "/whoami":
        return f"Your Telegram chat id is {chat_id}."
    if normalized in {"/start", "/help"}:
        return (
            "Hi, I’m CareLoop on Telegram. Tell me what you need help with.\n\n"
            "Try: I have a bad cough near USC. Can you book me a doctor tomorrow morning?\n\n"
            "Paid live searches use FET on the Fetch.ai stable testnet. After I quote a service fee, "
            "reply /pay (auto-pay from the demo wallet) or /paid <tx-hash> (after a manual transfer).\n\n"
            "Send /whoami if you want the chat id for TELEGRAM_ALLOWED_CHAT_IDS."
        )

    command, args = _split_command(normalized)
    sender = telegram_sender_id(chat_id)

    if command == "/pay":
        return _handle_pay_command(int(chat_id), sender, args)
    if command == "/paid":
        return _handle_paid_command(int(chat_id), sender, args)
    if command in {"/payment", "/paycard", "/fet"}:
        return _handle_payment_status(int(chat_id))

    response = orchestrator_chat_response(None, sender, normalized)
    response = response or "I’m still coordinating that. Please send one more detail."
    try:
        return _attach_pay_card(int(chat_id), sender, response)
    except (TypeError, ValueError):
        return response


def run_bridge() -> None:
    config = load_config()
    offset = int(os.getenv("TELEGRAM_START_OFFSET", "0") or "0")
    print("CareLoop Telegram bridge started.")
    if config.allowed_chat_ids:
        print(f"Allowed Telegram chat ids: {', '.join(sorted(config.allowed_chat_ids))}")
    else:
        print("Allowed Telegram chat ids: all chats. Set TELEGRAM_ALLOWED_CHAT_IDS to restrict the demo bot.")

    recipient = telegram_fet_recipient()
    if recipient:
        print(f"Telegram FET recipient: {recipient}")
    else:
        print("No Telegram FET recipient configured. Set TELEGRAM_FET_RECIPIENT to enable /pay and /paid.")

    while True:
        try:
            response = httpx.get(
                telegram_api_url(config.token, "getUpdates"),
                params={"timeout": 25, "offset": offset, "allowed_updates": ["message", "edited_message"]},
                timeout=35,
            )
            response.raise_for_status()
            payload = response.json()
            for update in payload.get("result", []):
                offset = int(update["update_id"]) + 1
                chat_id, text = _update_text(update)
                if chat_id is None or not text:
                    continue
                if not is_allowed_chat(config, chat_id):
                    send_message(config, chat_id, "This CareLoop demo bot is restricted to approved Telegram chats.")
                    continue
                answer = handle_text(config, chat_id, text)
                send_message(config, chat_id, answer)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Telegram bridge error: {exc}")
            time.sleep(max(config.poll_seconds, 3.0))

        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    run_bridge()
