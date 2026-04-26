import os
from decimal import Decimal

from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from config import create_careloop_agent, env_int
from domain import make_case_id
from models import CareRequest, CareResult

try:
    from cosmpy.aerial.client import LedgerClient, NetworkConfig
    from cosmpy.aerial.wallet import LocalWallet
    from cosmpy.crypto.address import Address
except ImportError:  # pragma: no cover - optional on-chain demo dependency
    LedgerClient = None
    NetworkConfig = None
    LocalWallet = None
    Address = None


AGENT_NAME = "careloop-payment-buyer-demo"
PORT = env_int("PAYMENT_BUYER_AGENT_PORT", 8017)
BUYER_MODE = os.getenv("PAYMENT_BUYER_MODE", "commit").lower()
BUYER_TASK = os.getenv("PAYMENT_BUYER_TASK", "otc_order").lower()
FET_ONCHAIN_PAYMENT = os.getenv("FET_ONCHAIN_PAYMENT", "false").lower() in {"1", "true", "yes", "on"}
FET_TESTNET_MNEMONIC = os.getenv("FET_TESTNET_MNEMONIC", "")
PHARMACY_AGENT_ADDRESS = (
    os.getenv("PHARMACY_ASSISTANT_AGENT_ADDRESS")
    or os.getenv("PRESCRIPTION_STATUS_AGENT_ADDRESS")
    or os.getenv("PHARMACY_AGENT_ADDRESS", "")
)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="PAYMENT_BUYER_AGENT_SEED",
    default_seed="careloop payment buyer demo seed phrase change me",
    description="CareLoop demo buyer for local FET Payment Protocol happy-path and rejection testing.",
)

payment_proto = Protocol(spec=payment_protocol_spec, role="buyer")


def _fet_amount_to_atestfet(amount: str) -> int:
    return int(Decimal(amount) * Decimal(10**18))


def _send_testnet_fet(recipient: str, amount: str, memo: str | None) -> str | None:
    if not FET_ONCHAIN_PAYMENT or not FET_TESTNET_MNEMONIC:
        return None
    if LedgerClient is None or NetworkConfig is None or LocalWallet is None or Address is None:
        return None
    if not recipient.startswith("fetch"):
        return None

    ledger = LedgerClient(NetworkConfig.fetchai_stable_testnet())
    wallet = LocalWallet.from_mnemonic(FET_TESTNET_MNEMONIC, prefix="fetch")
    tx = ledger.send_tokens(
        Address(recipient),
        _fet_amount_to_atestfet(amount),
        "atestfet",
        wallet,
        memo=memo,
    )
    return tx.tx_hash


@payment_proto.on_message(RequestPayment)
async def handle_request_payment(ctx: Context, sender: str, msg: RequestPayment):
    ctx.logger.info(f"{AGENT_NAME}: payment requested by {sender}: {msg}")
    if not msg.accepted_funds:
        await ctx.send(sender, RejectPayment(reason="No accepted funds were provided."))
        return

    selected = msg.accepted_funds[0]
    if BUYER_MODE == "reject":
        await ctx.send(sender, RejectPayment(reason="Demo buyer rejected payment."))
        return

    tx_hash = _send_testnet_fet(
        msg.recipient,
        selected.amount,
        msg.reference or msg.description,
    )
    transaction_id = tx_hash or f"demo-fet-tx-{msg.reference or 'no-ref'}"
    if tx_hash:
        ctx.logger.info(f"{AGENT_NAME}: sent on-chain testnet FET tx {tx_hash}")

    await ctx.send(
        sender,
        CommitPayment(
            funds=Funds(
                amount=selected.amount,
                currency=selected.currency,
                payment_method=selected.payment_method,
            ),
            recipient=msg.recipient,
            transaction_id=transaction_id,
            reference=msg.reference,
            description=msg.description,
            metadata=msg.metadata or {},
        ),
    )


@payment_proto.on_message(CompletePayment)
async def handle_complete_payment(ctx: Context, sender: str, msg: CompletePayment):
    ctx.logger.info(f"{AGENT_NAME}: payment completed by {sender}: {msg.transaction_id}")


@payment_proto.on_message(CancelPayment)
async def handle_cancel_payment(ctx: Context, sender: str, msg: CancelPayment):
    ctx.logger.info(f"{AGENT_NAME}: payment canceled by {sender}: {msg.reason}")


@agent.on_message(model=CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: received paid result from {sender}: {msg.summary}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    if PHARMACY_AGENT_ADDRESS:
        text = "Find the best allergy medicine near Westwood and order it for delivery."
        context = {"location": "Westwood, Los Angeles, CA", "preference": "delivery"}
        await ctx.send(
            PHARMACY_AGENT_ADDRESS,
            CareRequest(
                case_id=make_case_id("payment-demo"),
                user_id=ctx.agent.address,
                text=text,
                context=context,
            ),
        )
        ctx.logger.info(f"{AGENT_NAME}: sent demo CareRequest to pharmacy agent")


agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
