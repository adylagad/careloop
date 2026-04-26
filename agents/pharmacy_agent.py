import os
import re

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
    build_otc_order_quote,
    format_otc_order_preview,
    is_otc_order_intent,
    is_pharmacy_status_intent,
    make_case_id,
    otc_order_paid_result,
    otc_order_unpaid_result,
    result_to_text,
)
from models import CareRequest, CareResult, PharmacyOrderQuote


AGENT_NAME = "careloop-pharmacy-assistant"
PORT = env_int("PHARMACY_AGENT_PORT", 8011)

if not os.getenv("PHARMACY_ASSISTANT_AGENT_SEED"):
    os.environ["PHARMACY_ASSISTANT_AGENT_SEED"] = (
        os.getenv("PRESCRIPTION_STATUS_AGENT_SEED")
        or os.getenv("PHARMACY_AGENT_SEED")
        or "careloop pharmacy options seed phrase change me"
    )

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="PHARMACY_ASSISTANT_AGENT_SEED",
    default_seed="careloop pharmacy options seed phrase change me",
    description=(
        "CareLoop Pharmacy Assistant recommends and prepares over-the-counter medicine "
        "orders with FET payment and checkout handoff."
    ),
)

care_proto = Protocol(name="CareLoopPharmacyAssistantProtocol", version="0.1.0")
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")
pending_orders: dict[str, tuple[str, CareRequest, PharmacyOrderQuote]] = {}


def _context_from_chat_text(text: str) -> dict[str, str]:
    normalized = text.lower()
    preference = "delivery" if "deliver" in normalized or "delivery" in normalized else "pickup"
    context = {"location": "Los Angeles, CA", "preference": preference}
    for marker in ["address:", "near:", "location:"]:
        if marker in normalized:
            value = text[normalized.index(marker) + len(marker):].splitlines()[0].strip(" .")
            if value:
                context["address"] = value
    if "address" not in context:
        place_match = re.search(
            r"\b(?:near|around|in|to)\s+([A-Za-z][A-Za-z .,-]{2,60}?)(?:\s+and\s+|\s+for\s+|\s+with\s+|[.!?]|$)",
            text,
            re.IGNORECASE,
        )
        if place_match:
            context["address"] = place_match.group(1).strip(" .,")
    return context


def pharmacy_chat_response(ctx: Context, sender: str, text: str) -> str:
    normalized = " ".join(text.lower().split())
    if normalized in {"hi", "hello", "hey", "help", "what can you do"}:
        return (
            "Hi, I’m CareLoop’s OTC pharmacy assistant. Tell me what symptom or OTC "
            "medicine you need and your address area, and I’ll recommend an option and prepare checkout.\n\n"
            "Examples:\n"
            "`Find the best allergy medicine near Westwood.`\n"
            "`Order Tylenol for delivery to Santa Monica.`"
        )

    if is_pharmacy_status_intent(text):
        return (
            "I only handle over-the-counter medicine recommendations and orders. "
            "Prescription status belongs in the CareLoop orchestrator flow, where it can use the patient's care context."
        )

    request = CareRequest(
        case_id=make_case_id("chat-pharmacy"),
        user_id=sender,
        text=text,
        context=_context_from_chat_text(text),
    )

    if not is_otc_order_intent(text):
        return (
            "I can help with over-the-counter medicine only. Try asking something like "
            "`Find the best allergy medicine near Westwood` or `Order Tylenol for delivery`."
        )

    order = build_otc_order_quote(request)
    return format_otc_order_preview(order)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    if not is_otc_order_intent(msg.text):
        await ctx.send(
            sender,
            CareResult(
                case_id=msg.case_id,
                agent_name=AGENT_NAME,
                status="unsupported_intent",
                summary="This specialist only handles over-the-counter medicine recommendations and orders.",
                next_actions=[
                    "Route prescription status requests to the CareLoop orchestrator.",
                    "Ask this agent for OTC medicine by symptom, medicine name, and address area.",
                ],
                timeline_events=["Unsupported pharmacy-assistant intent"],
            ),
        )
        return

    order = build_otc_order_quote(msg)
    quote = order.payment_quote
    pending_orders[quote.reference] = (sender, msg, order)
    ctx.logger.info(f"{AGENT_NAME}: requesting {quote.amount} {quote.currency} for OTC order from {sender}")
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
                "product": order.product.name,
                "provider": order.product.provider,
            },
        ),
    )


@payment_proto.on_message(CommitPayment)
async def handle_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    reference = msg.reference or ""
    pending_order = pending_orders.pop(reference, None)
    if pending_order is not None:
        original_sender, request, order = pending_order
        result = otc_order_paid_result(request, order)
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        await ctx.send(original_sender, result)
        ctx.logger.info(f"{AGENT_NAME}: OTC order payment completed for {reference}")
        return

    ctx.logger.warning(f"{AGENT_NAME}: unknown payment reference {reference}")
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))


@payment_proto.on_message(RejectPayment)
async def handle_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    matching_order_reference = next(
        (
            reference
            for reference, (pending_sender, _, _) in pending_orders.items()
            if pending_sender == sender
        ),
        None,
    )
    if matching_order_reference is not None:
        _, request, _ = pending_orders.pop(matching_order_reference)
        reason = msg.reason or "buyer rejected payment"
        await ctx.send(sender, otc_order_unpaid_result(request, reason))
        ctx.logger.info(f"{AGENT_NAME}: OTC order payment rejected for {matching_order_reference}: {reason}")
        return

    ctx.logger.warning(f"{AGENT_NAME}: reject from {sender} had no pending request")


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: received result from {sender}: {msg.status}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Pharmacy Assistant")


agent.include(create_chat_protocol(AGENT_NAME, pharmacy_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
