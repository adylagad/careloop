import os

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


AGENT_NAME = "careloop-payment-buyer-demo"
PORT = env_int("PAYMENT_BUYER_AGENT_PORT", 8017)
BUYER_MODE = os.getenv("PAYMENT_BUYER_MODE", "commit").lower()
PHARMACY_AGENT_ADDRESS = os.getenv("PRESCRIPTION_STATUS_AGENT_ADDRESS") or os.getenv("PHARMACY_AGENT_ADDRESS", "")

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="PAYMENT_BUYER_AGENT_SEED",
    default_seed="careloop payment buyer demo seed phrase change me",
    description="CareLoop demo buyer for local FET Payment Protocol happy-path and rejection testing.",
)

payment_proto = Protocol(spec=payment_protocol_spec, role="buyer")


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

    await ctx.send(
        sender,
        CommitPayment(
            funds=Funds(
                amount=selected.amount,
                currency=selected.currency,
                payment_method=selected.payment_method,
            ),
            recipient=msg.recipient,
            transaction_id=f"demo-fet-tx-{msg.reference or 'no-ref'}",
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
        await ctx.send(
            PHARMACY_AGENT_ADDRESS,
            CareRequest(
                case_id=make_case_id("payment-demo"),
                user_id=ctx.agent.address,
                text="Keep checking whether my prescription is ready at CVS Westwood for pickup.",
                context={
                    "location": "Los Angeles, CA",
                    "preference": "pickup",
                    "pharmacy_name": "CVS Pharmacy - Westwood Blvd",
                },
            ),
        )
        ctx.logger.info(f"{AGENT_NAME}: sent demo CareRequest to pharmacy agent")


agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
