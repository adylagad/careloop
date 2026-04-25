from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import (
    build_pharmacy_recommendation,
    format_pharmacy_preview,
    make_case_id,
    pharmacy_paid_result,
    pharmacy_unpaid_result,
    result_to_text,
)
from models import CareRequest, CareResult, PharmacyRecommendation


AGENT_NAME = "careloop-pharmacy-options"
PORT = env_int("PHARMACY_AGENT_PORT", 8011)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="PHARMACY_AGENT_SEED",
    default_seed="careloop pharmacy options seed phrase change me",
    description=(
        "CareLoop Pharmacy Navigator compares mocked pharmacy fulfillment options "
        "and demonstrates FET Payment Protocol for a paid specialist service."
    ),
)

care_proto = Protocol(name="CareLoopPharmacyOptionsProtocol", version="0.1.0")
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")
pending_recommendations: dict[str, tuple[str, CareRequest, PharmacyRecommendation]] = {}


def pharmacy_chat_response(ctx: Context, sender: str, text: str) -> str:
    request = CareRequest(
        case_id=make_case_id("chat-pharmacy"),
        user_id=sender,
        text=text,
        context={"location": "Los Angeles, CA", "preference": "delivery"},
    )
    recommendation = build_pharmacy_recommendation(request)
    return format_pharmacy_preview(recommendation)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    recommendation = build_pharmacy_recommendation(msg)
    quote = recommendation.payment_quote
    pending_recommendations[quote.reference] = (sender, msg, recommendation)
    ctx.logger.info(f"{AGENT_NAME}: requesting {quote.amount} {quote.currency} from {sender}")

    await ctx.send(
        sender,
        RequestPayment(
            accepted_funds=[
                Funds(
                    amount=quote.amount,
                    currency=quote.currency,
                    payment_method=quote.payment_method,
                )
            ],
            recipient=ctx.agent.address,
            deadline_seconds=300,
            reference=quote.reference,
            description=f"{quote.service_name} service fee",
            metadata={
                "case_id": quote.case_id,
                "agent": AGENT_NAME,
                "service_name": quote.service_name,
            },
        ),
    )


@payment_proto.on_message(CommitPayment)
async def handle_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    reference = msg.reference or ""
    pending = pending_recommendations.pop(reference, None)
    if pending is None:
        ctx.logger.warning(f"{AGENT_NAME}: unknown payment reference {reference}")
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        return

    original_sender, request, recommendation = pending
    result = pharmacy_paid_result(request, recommendation)
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    await ctx.send(original_sender, result)
    ctx.logger.info(f"{AGENT_NAME}: payment completed for {reference}")


@payment_proto.on_message(RejectPayment)
async def handle_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    matching_reference = next(
        (
            reference
            for reference, (pending_sender, _, _) in pending_recommendations.items()
            if pending_sender == sender
        ),
        None,
    )
    if matching_reference is None:
        ctx.logger.warning(f"{AGENT_NAME}: reject from {sender} had no pending request")
        return

    _, request, _ = pending_recommendations.pop(matching_reference)
    reason = msg.reason or "buyer rejected payment"
    await ctx.send(sender, pharmacy_unpaid_result(request, reason))
    ctx.logger.info(f"{AGENT_NAME}: payment rejected for {matching_reference}: {reason}")


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: received result from {sender}: {msg.status}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Pharmacy Navigator")


agent.include(create_chat_protocol(AGENT_NAME, pharmacy_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
