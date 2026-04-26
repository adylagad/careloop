import os
import re
from dataclasses import dataclass

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
from chat_protocol import create_text_chat
from config import create_careloop_agent, env_int
from domain import (
    build_otc_order_quote,
    build_otc_service_payment_quote,
    format_otc_payment_prompt,
    format_otc_order_preview,
    is_otc_order_intent,
    is_pharmacy_status_intent,
    make_case_id,
    otc_order_paid_result,
    otc_order_unpaid_result,
    result_to_text,
)
from llm import asi_chat_completion
from models import CareRequest, CareResult, PaymentQuote, PharmacyOrderQuote
from pharmacy_data import nearby_pharmacies


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
PHARMACY_CONTEXT_BY_SENDER: dict[str, PharmacyOrderQuote] = {}
MAX_PHARMACY_CONTEXTS = 100


@dataclass
class PickupAnswer:
    address: str
    pharmacies: list[str]


@dataclass
class PendingOrderPayment:
    original_sender: str
    request: CareRequest
    quote: PaymentQuote
    response_channel: str


pending_orders: dict[str, PendingOrderPayment] = {}


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


def _remember_order(sender: str, order: PharmacyOrderQuote) -> None:
    if len(PHARMACY_CONTEXT_BY_SENDER) >= MAX_PHARMACY_CONTEXTS:
        oldest_sender = next(iter(PHARMACY_CONTEXT_BY_SENDER))
        PHARMACY_CONTEXT_BY_SENDER.pop(oldest_sender, None)
    PHARMACY_CONTEXT_BY_SENDER[sender] = order


def _extract_place(text: str) -> str | None:
    match = re.search(
        r"\b(?:near|around|in|to)\s+([A-Za-z][A-Za-z .,-]{2,60}?)(?:\s+where\b|\s+that\b|\s+for\b|\s+and\b|[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip(" .,")
    return None


def _pickup_answer_from_followup(question: str, order: PharmacyOrderQuote) -> PickupAnswer:
    address = _extract_place(question) or order.address_hint
    pharmacies = nearby_pharmacies(address, limit=5)
    return PickupAnswer(address=address, pharmacies=pharmacies)


def _fallback_followup_answer(question: str, order: PharmacyOrderQuote) -> str:
    normalized = " ".join(question.lower().split())
    if any(term in normalized for term in ["nearest", "closest", "store", "collect", "pickup", "pick up", "not online"]):
        pickup = _pickup_answer_from_followup(question, order)
        if pickup.pharmacies:
            options = "\n".join(f"- {item}" for item in pickup.pharmacies)
            nearest = pickup.pharmacies[0]
        else:
            options = "- I could not fetch live nearby pharmacy locations right now."
            nearest = "nearest store unavailable"
        return (
            f"For pickup near {pickup.address}, the nearest real pharmacy I found is: {nearest}.\n\n"
            f"Nearby options:\n{options}\n\n"
            f"Medicine context: {order.product.name} ({order.product.active_ingredient} {order.product.strength}). "
            f"Real online quote I found earlier: {order.subtotal_usd} from {order.product.price_source}. "
            "Local store inventory and shelf price are not public through OpenStreetMap, so confirm availability at the store before going.\n\n"
            f"Safety note: {order.product.safety_note}"
        )

    return (
        f"I’m using your last OTC recommendation: {order.product.name} for {order.user_need}. "
        f"The real quoted subtotal is {order.subtotal_usd} from {order.product.price_source}. "
        "I can help compare pickup locations, explain why this option fits, or prepare checkout."
    )


async def _send_otc_payment_request(ctx: Context, sender: str, quote: PaymentQuote) -> None:
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
            recipient=os.getenv("PHARMACY_ASSISTANT_FET_WALLET_ADDRESS", ctx.agent.address),
            deadline_seconds=300,
            reference=quote.reference,
            description=f"{quote.service_name} service fee",
            metadata={
                "case_id": quote.case_id,
                "agent": AGENT_NAME,
                "service_name": quote.service_name,
                "payment_gate": "before_live_price_search",
            },
        ),
    )


def _answer_followup(sender: str, question: str, order: PharmacyOrderQuote) -> str:
    pickup = _pickup_answer_from_followup(question, order)
    system_prompt = (
        "You are CareLoop Pharmacy Assistant, an OTC-only pharmacy ordering assistant. "
        "Use the user's previous OTC recommendation context. Be concise, do not diagnose, "
        "do not claim local store inventory or local shelf price unless provided. If pickup is requested, "
        "use the provided OpenStreetMap pharmacy list and say local inventory and shelf price should be confirmed. "
        "Compare online and offline options when asked: online has real quote data, offline has real locations but no free live shelf-price API."
    )
    pharmacy_lines = "\n".join(f"- {item}" for item in pickup.pharmacies) or "- none fetched"
    user_prompt = (
        f"Previous OTC recommendation: {order.product.name} ({order.product.active_ingredient} {order.product.strength})\n"
        f"Need: {order.user_need}\n"
        f"Real online quote: {order.subtotal_usd}\n"
        f"Price source: {order.product.price_source}\n"
        f"Checkout URL: {order.product.checkout_url}\n"
        f"Previous address area: {order.address_hint}\n"
        f"Follow-up address area: {pickup.address}\n"
        f"Nearby real pharmacies from OpenStreetMap:\n{pharmacy_lines}\n\n"
        f"User follow-up: {question}"
    )
    llm_answer = asi_chat_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        session_id=f"careloop-pharmacy-{sender}",
        max_tokens=450,
    )
    return llm_answer or _fallback_followup_answer(question, order)


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

    existing_order = PHARMACY_CONTEXT_BY_SENDER.get(sender)
    if existing_order and not is_otc_order_intent(text):
        return _answer_followup(sender, text, existing_order)

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

    quote = build_otc_service_payment_quote(request)
    return format_otc_payment_prompt(request, quote)


async def pharmacy_chat_handler(ctx: Context, sender: str, text: str) -> str:
    normalized = " ".join(text.lower().split())
    if normalized in {"hi", "hello", "hey", "help", "what can you do"}:
        return pharmacy_chat_response(ctx, sender, text)

    if is_pharmacy_status_intent(text):
        return pharmacy_chat_response(ctx, sender, text)

    existing_order = PHARMACY_CONTEXT_BY_SENDER.get(sender)
    if existing_order and not is_otc_order_intent(text):
        return _answer_followup(sender, text, existing_order)

    request = CareRequest(
        case_id=make_case_id("chat-pharmacy"),
        user_id=sender,
        text=text,
        context=_context_from_chat_text(text),
    )

    if not is_otc_order_intent(text):
        return pharmacy_chat_response(ctx, sender, text)

    quote = build_otc_service_payment_quote(request)
    pending_orders[quote.reference] = PendingOrderPayment(
        original_sender=sender,
        request=request,
        quote=quote,
        response_channel="chat",
    )
    await _send_otc_payment_request(ctx, sender, quote)
    return format_otc_payment_prompt(request, quote)


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

    quote = build_otc_service_payment_quote(msg)
    pending_orders[quote.reference] = PendingOrderPayment(
        original_sender=sender,
        request=msg,
        quote=quote,
        response_channel="care",
    )
    ctx.logger.info(f"{AGENT_NAME}: requesting {quote.amount} {quote.currency} for OTC order from {sender}")
    await _send_otc_payment_request(ctx, sender, quote)


@payment_proto.on_message(CommitPayment)
async def handle_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    reference = msg.reference or ""
    pending_payment = pending_orders.pop(reference, None)
    if pending_payment is not None:
        original_sender = pending_payment.original_sender
        request = pending_payment.request
        order = build_otc_order_quote(request, pending_payment.quote)
        _remember_order(original_sender, order)
        result = otc_order_paid_result(request, order)
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        if pending_payment.response_channel == "chat":
            await ctx.send(original_sender, create_text_chat(format_otc_order_preview(order)))
        else:
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
            for reference, pending in pending_orders.items()
            if pending.original_sender == sender
        ),
        None,
    )
    if matching_order_reference is not None:
        pending_payment = pending_orders.pop(matching_order_reference)
        request = pending_payment.request
        reason = msg.reason or "buyer rejected payment"
        unpaid = otc_order_unpaid_result(request, reason)
        if pending_payment.response_channel == "chat":
            await ctx.send(sender, create_text_chat(unpaid.summary))
        else:
            await ctx.send(sender, unpaid)
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


agent.include(create_chat_protocol(AGENT_NAME, pharmacy_chat_handler), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
