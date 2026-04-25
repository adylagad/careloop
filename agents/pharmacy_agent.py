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
    build_pharmacy_fulfillment_status,
    format_pharmacy_fulfillment_preview,
    make_case_id,
    pharmacy_monitoring_result,
    pharmacy_status_update_result,
    pharmacy_unpaid_result,
    result_to_text,
)
from models import CareRequest, CareResult, PharmacyFulfillmentStatus


AGENT_NAME = "careloop-pharmacy-options"
PORT = env_int("PHARMACY_AGENT_PORT", 8011)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="PHARMACY_AGENT_SEED",
    default_seed="careloop pharmacy options seed phrase change me",
    description=(
        "CareLoop Pharmacy Fulfillment checks whether a doctor-sent prescription "
        "is received, delayed, ready, or needs action, with paid FET monitoring."
    ),
)

care_proto = Protocol(name="CareLoopPharmacyOptionsProtocol", version="0.1.0")
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")
pending_fulfillments: dict[str, tuple[str, CareRequest, PharmacyFulfillmentStatus]] = {}
active_monitors: dict[str, tuple[str, CareRequest, int]] = {}


def _context_from_chat_text(text: str) -> dict[str, str]:
    normalized = text.lower()
    preference = "delivery" if "deliver" in normalized or "delivery" in normalized else "pickup"
    context = {"location": "Los Angeles, CA", "preference": preference}
    for marker in ["pharmacy:", "sent to:", "at pharmacy:"]:
        if marker in normalized:
            value = text[normalized.index(marker) + len(marker):].splitlines()[0].strip(" .")
            if value:
                context["pharmacy_name"] = value
    return context


def pharmacy_chat_response(ctx: Context, sender: str, text: str) -> str:
    normalized = " ".join(text.lower().split())
    if normalized in {"hi", "hello", "hey", "help", "what can you do"}:
        return (
            "Hi, I’m CareLoop’s pharmacy fulfillment helper. Ask me if a doctor-sent "
            "prescription is ready for pickup or delivery.\n\n"
            "Example: `My doctor sent Metformin 500 mg to CVS Westwood. Is it ready for pickup?`"
        )

    request = CareRequest(
        case_id=make_case_id("chat-pharmacy"),
        user_id=sender,
        text=text,
        context=_context_from_chat_text(text),
    )
    fulfillment = build_pharmacy_fulfillment_status(request)
    return format_pharmacy_fulfillment_preview(fulfillment)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    fulfillment = build_pharmacy_fulfillment_status(msg)
    quote = fulfillment.payment_quote
    pending_fulfillments[quote.reference] = (sender, msg, fulfillment)
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
            description=f"{quote.service_name} active status monitoring fee",
            metadata={
                "case_id": quote.case_id,
                "agent": AGENT_NAME,
                "service_name": quote.service_name,
                "medication": fulfillment.medication,
                "pharmacy_name": fulfillment.pharmacy_name,
            },
        ),
    )


@payment_proto.on_message(CommitPayment)
async def handle_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    reference = msg.reference or ""
    pending = pending_fulfillments.pop(reference, None)
    if pending is None:
        ctx.logger.warning(f"{AGENT_NAME}: unknown payment reference {reference}")
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        return

    original_sender, request, fulfillment = pending
    result = pharmacy_monitoring_result(request, fulfillment)
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    await ctx.send(original_sender, result)
    if not fulfillment.status.startswith("ready"):
        active_monitors[reference] = (original_sender, request, 0)
        ctx.logger.info(f"{AGENT_NAME}: active monitor started for {reference}")
    ctx.logger.info(f"{AGENT_NAME}: payment completed for {reference}")


@payment_proto.on_message(RejectPayment)
async def handle_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    matching_reference = next(
        (
            reference
            for reference, (pending_sender, _, _) in pending_fulfillments.items()
            if pending_sender == sender
        ),
        None,
    )
    if matching_reference is None:
        ctx.logger.warning(f"{AGENT_NAME}: reject from {sender} had no pending request")
        return

    _, request, _ = pending_fulfillments.pop(matching_reference)
    reason = msg.reason or "buyer rejected payment"
    await ctx.send(sender, pharmacy_unpaid_result(request, reason))
    ctx.logger.info(f"{AGENT_NAME}: payment rejected for {matching_reference}: {reason}")


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: received result from {sender}: {msg.status}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Pharmacy Fulfillment")


@agent.on_interval(period=30.0)
async def check_active_monitors(ctx: Context):
    completed: list[str] = []
    for reference, (recipient, request, tick) in list(active_monitors.items()):
        next_tick = tick + 1
        status = build_pharmacy_fulfillment_status(request, monitor_tick=next_tick)
        active_monitors[reference] = (recipient, request, next_tick)
        if status.status.startswith("ready") or status.status == "action_needed":
            await ctx.send(recipient, pharmacy_status_update_result(request, status))
            completed.append(reference)
            ctx.logger.info(f"{AGENT_NAME}: monitor {reference} sent terminal update {status.status}")
        else:
            ctx.logger.info(f"{AGENT_NAME}: monitor {reference} still {status.status}")

    for reference in completed:
        active_monitors.pop(reference, None)


agent.include(create_chat_protocol(AGENT_NAME, pharmacy_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
