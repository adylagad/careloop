import os
import time
from dataclasses import dataclass
from typing import Any

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

from chat_protocol import create_chat_protocol, create_text_chat
from config import create_careloop_agent, env_int
from domain import (
    APPOINTMENT_SERVICE_FEE_FET,
    appointment_paid_result,
    appointment_unpaid_result,
    build_appointment_payment_quote,
    build_appointment_search_quote,
    format_appointment_payment_prompt,
    format_appointment_search_preview,
    infer_appointment_specialty,
    infer_appointment_urgency,
    infer_insurance,
    infer_location,
    is_appointment_intent,
    make_case_id,
)
from llm import asi_chat_completion
from models import AppointmentSearchQuote, CareRequest, CareResult, PaymentQuote


AGENT_NAME = "careloop-appointment-assistant"
PORT = env_int("APPOINTMENT_AGENT_PORT", 8013)

if not os.getenv("APPOINTMENT_ASSISTANT_AGENT_SEED"):
    os.environ["APPOINTMENT_ASSISTANT_AGENT_SEED"] = (
        os.getenv("APPOINTMENT_AGENT_SEED")
        or "careloop appointment booking seed phrase change me"
    )

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="APPOINTMENT_ASSISTANT_AGENT_SEED",
    default_seed="careloop appointment booking seed phrase change me",
    description=(
        "CareLoop Appointment Assistant runs paid live appointment/provider searches, "
        "returns real booking handoff links, and keeps chat context for follow-up questions."
    ),
    readme_path="agents/readmes/appointment_assistant.md",
)

care_proto = Protocol(name="CareLoopAppointmentAssistantProtocol", version="0.2.0")
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")
APPOINTMENT_CONTEXT_BY_SENDER: dict[str, AppointmentSearchQuote] = {}
MAX_APPOINTMENT_CONTEXTS = 100
PAYMENT_REQUEST_DEADLINE_SECONDS = 300
PAYMENT_EXPIRY_SECONDS = PAYMENT_REQUEST_DEADLINE_SECONDS - 15
PAYMENT_REQUEST_VERSION = "appointment-fet-direct-card-v2"


@dataclass
class PendingAppointmentPayment:
    original_sender: str
    request: CareRequest
    quote: PaymentQuote
    response_channel: str
    request_fingerprint: str
    created_at: float
    request_version: str


pending_appointments: dict[str, PendingAppointmentPayment] = {}
pending_by_sender: dict[str, str] = {}
paid_request_by_sender: dict[str, str] = {}


def _model_dump(model) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)


def _context_from_chat_text(text: str) -> dict[str, str]:
    return {
        "specialty": infer_appointment_specialty(text),
        "location": infer_location(text),
        "urgency": infer_appointment_urgency(text),
        **({"insurance": infer_insurance(text)} if infer_insurance(text) else {}),
    }


def _request_fingerprint(request: CareRequest) -> str:
    context = request.context or {}
    parts = [
        " ".join(request.text.lower().split()),
        str(context.get("specialty") or infer_appointment_specialty(request.text)).lower(),
        str(context.get("location") or infer_location(request.text)).lower(),
        str(context.get("insurance") or infer_insurance(request.text) or "").lower(),
        str(context.get("urgency") or infer_appointment_urgency(request.text)).lower(),
    ]
    return "|".join(part.strip() for part in parts)


def _pending_key(reference: str) -> str:
    return f"appointment:pending:{reference}"


def _pending_by_sender_key(sender: str) -> str:
    return f"appointment:pending-by-sender:{sender}"


def _paid_key(sender: str) -> str:
    return f"appointment:paid-search:{sender}"


def _paid_fingerprint_key(sender: str) -> str:
    return f"appointment:paid-fingerprint:{sender}"


def _pending_to_dict(pending: PendingAppointmentPayment) -> dict[str, Any]:
    return {
        "original_sender": pending.original_sender,
        "request": _model_dump(pending.request),
        "quote": _model_dump(pending.quote),
        "response_channel": pending.response_channel,
        "request_fingerprint": pending.request_fingerprint,
        "created_at": pending.created_at,
        "request_version": pending.request_version,
    }


def _pending_from_dict(data: dict[str, Any]) -> PendingAppointmentPayment:
    return PendingAppointmentPayment(
        original_sender=str(data["original_sender"]),
        request=CareRequest(**data["request"]),
        quote=PaymentQuote(**data["quote"]),
        response_channel=str(data["response_channel"]),
        request_fingerprint=str(data["request_fingerprint"]),
        created_at=float(data["created_at"]),
        request_version=str(data.get("request_version") or "legacy"),
    )


def _is_pending_expired(pending: PendingAppointmentPayment) -> bool:
    return time.time() - pending.created_at > PAYMENT_EXPIRY_SECONDS


def _remember_search(sender: str, search: AppointmentSearchQuote) -> None:
    if len(APPOINTMENT_CONTEXT_BY_SENDER) >= MAX_APPOINTMENT_CONTEXTS:
        oldest_sender = next(iter(APPOINTMENT_CONTEXT_BY_SENDER))
        APPOINTMENT_CONTEXT_BY_SENDER.pop(oldest_sender, None)
    APPOINTMENT_CONTEXT_BY_SENDER[sender] = search


def _store_pending(ctx: Context | None, pending: PendingAppointmentPayment) -> None:
    old_reference = pending_by_sender.pop(pending.original_sender, None)
    if old_reference:
        pending_appointments.pop(old_reference, None)
        if ctx is not None:
            ctx.storage.remove(_pending_key(old_reference))

    pending_appointments[pending.quote.reference] = pending
    pending_by_sender[pending.original_sender] = pending.quote.reference
    if ctx is not None:
        ctx.storage.set(_pending_key(pending.quote.reference), _pending_to_dict(pending))
        ctx.storage.set(_pending_by_sender_key(pending.original_sender), pending.quote.reference)


def _remove_pending(ctx: Context | None, pending: PendingAppointmentPayment) -> None:
    pending_appointments.pop(pending.quote.reference, None)
    if pending_by_sender.get(pending.original_sender) == pending.quote.reference:
        pending_by_sender.pop(pending.original_sender, None)
    if ctx is not None:
        ctx.storage.remove(_pending_key(pending.quote.reference))
        if ctx.storage.get(_pending_by_sender_key(pending.original_sender)) == pending.quote.reference:
            ctx.storage.remove(_pending_by_sender_key(pending.original_sender))


def _load_pending_by_reference(ctx: Context | None, reference: str) -> PendingAppointmentPayment | None:
    pending = pending_appointments.get(reference)
    if pending is None and ctx is not None:
        data = ctx.storage.get(_pending_key(reference))
        if data:
            pending = _pending_from_dict(data)
            pending_appointments[reference] = pending
            pending_by_sender[pending.original_sender] = reference
    if pending and _is_pending_expired(pending):
        _remove_pending(ctx, pending)
        return None
    return pending


def _load_pending_by_sender(ctx: Context | None, sender: str) -> PendingAppointmentPayment | None:
    reference = pending_by_sender.get(sender)
    if reference is None and ctx is not None:
        reference = ctx.storage.get(_pending_by_sender_key(sender))
        if reference:
            pending_by_sender[sender] = reference
    if not reference:
        return None
    return _load_pending_by_reference(ctx, reference)


def _store_paid_search(ctx: Context | None, sender: str, fingerprint: str, search: AppointmentSearchQuote) -> None:
    _remember_search(sender, search)
    paid_request_by_sender[sender] = fingerprint
    if ctx is not None:
        ctx.storage.set(_paid_key(sender), _model_dump(search))
        ctx.storage.set(_paid_fingerprint_key(sender), fingerprint)


def _load_paid_search(ctx: Context | None, sender: str) -> tuple[str | None, AppointmentSearchQuote | None]:
    fingerprint = paid_request_by_sender.get(sender)
    search = APPOINTMENT_CONTEXT_BY_SENDER.get(sender)
    if ctx is not None:
        fingerprint = fingerprint or ctx.storage.get(_paid_fingerprint_key(sender))
        if search is None:
            data = ctx.storage.get(_paid_key(sender))
            if data:
                search = AppointmentSearchQuote(**data)
                APPOINTMENT_CONTEXT_BY_SENDER[sender] = search
    return fingerprint, search


def _pending_requires_refresh(pending: PendingAppointmentPayment, request_fingerprint: str) -> bool:
    return (
        pending.request_fingerprint != request_fingerprint
        or pending.request_version != PAYMENT_REQUEST_VERSION
        or pending.quote.amount != APPOINTMENT_SERVICE_FEE_FET
        or pending.quote.currency != "FET"
        or pending.quote.payment_method != "fet_direct"
    )


def _pending_payment_message(pending: PendingAppointmentPayment) -> str:
    return (
        "💳 I already created a payment request for this appointment search, so I resent the same Pay option.\n\n"
        f"**Amount:** {pending.quote.amount} {pending.quote.currency}\n"
        f"**Reference:** `{pending.quote.reference}`\n\n"
        "Please click the Pay option in this chat. After payment, I’ll run the live provider/booking search "
        "and send the result here."
    )


async def _send_appointment_payment_request(ctx: Context, sender: str, quote: PaymentQuote) -> None:
    use_testnet = os.getenv("FET_USE_TESTNET", "true").lower() == "true"
    agent_wallet_address = ""
    try:
        agent_wallet_address = str(agent.wallet.address())
    except Exception:
        agent_wallet_address = ""
    recipient = agent_wallet_address or str(ctx.agent.address)
    metadata: dict[str, str] = {
        "agent": AGENT_NAME,
        "service": "careloop_appointment_search",
        "fet_network": "stable-testnet" if use_testnet else "mainnet",
        "mainnet": "false" if use_testnet else "true",
        "content": (
            "Please complete the FET payment to run the live appointment search. "
            "After payment, CareLoop will look up real providers and booking links."
        ),
    }
    if agent_wallet_address:
        metadata["provider_agent_wallet"] = agent_wallet_address

    payment_request = RequestPayment(
        accepted_funds=[
            Funds(
                amount=quote.amount,
                currency=quote.currency,
                payment_method=quote.payment_method,
            )
        ],
        recipient=recipient,
        deadline_seconds=PAYMENT_REQUEST_DEADLINE_SECONDS,
        reference=quote.reference,
        description=f"{quote.service_name} service fee",
        metadata=metadata,
    )
    ctx.logger.info(f"{AGENT_NAME}: sending native FET payment request to {sender}: {payment_request}")
    await ctx.send(sender, payment_request)


def _intro_message() -> str:
    return (
        "🩺 **Hi, I’m CareLoop Appointment Assistant.** I find real doctors/clinics and booking links, then help you "
        "choose the next step.\n\n"
        "**Try:**\n"
        "`Find a primary care doctor near USC Village this week with Medicare.`\n"
        "`Find a dermatologist near Westwood.`\n\n"
        "💳 I charge a small FET service fee before running the live search."
    )


def _answer_followup(sender: str, question: str, search: AppointmentSearchQuote) -> str:
    deterministic = _deterministic_followup_answer(question, search)
    if deterministic:
        return deterministic

    option_lines = "\n".join(
        (
            f"{index}. {option.provider_name}; {option.specialty}; {option.location}; "
            f"availability={option.earliest_available}; cost={option.estimated_cost}; "
            f"url={option.booking_url}; phone={option.phone or 'not published'}"
        )
        for index, option in enumerate(search.options, start=1)
    )
    fallback = format_appointment_search_preview(search)
    llm_answer = asi_chat_completion(
        system_prompt=(
            "You are CareLoop Appointment Assistant. Answer follow-up questions using only the known appointment "
            "search results. Be concise. Do not invent appointment slots, prices, accepted insurance, or confirmed bookings. "
            "If the user wants to book, point them to the booking URL and say final confirmation happens there unless a real booking API is configured."
        ),
        user_prompt=(
            f"Search context: specialty={search.specialty}, location={search.location}, "
            f"insurance={search.insurance or 'not specified'}, urgency={search.urgency}\n\n"
            f"Options:\n{option_lines}\n\n"
            f"User follow-up: {question}"
        ),
        session_id=f"careloop-appointment-{sender}",
        max_tokens=450,
    )
    return llm_answer or fallback


def _is_followup(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    followup_terms = [
        "closest",
        "nearest",
        "link",
        "url",
        "takes",
        "accepts",
        "medicare",
        "insurance",
        "daughter",
        "son",
        "caregiver",
        "tell",
        "send",
        "book",
        "first one",
        "second one",
        "third one",
        "what should i bring",
        "referral",
        "order",
    ]
    return any(term in normalized for term in followup_terms)


def _option_by_ordinal(text: str, search: AppointmentSearchQuote):
    normalized = " ".join(text.lower().split())
    if not search.options:
        return None
    if "second" in normalized or "2" in normalized:
        return search.options[1] if len(search.options) > 1 else search.options[0]
    if "third" in normalized or "3" in normalized:
        return search.options[2] if len(search.options) > 2 else search.options[0]
    return search.options[0]


def _deterministic_followup_answer(question: str, search: AppointmentSearchQuote) -> str | None:
    normalized = " ".join(question.lower().split())
    selected = _option_by_ordinal(question, search)
    if selected is None:
        return "I do not have appointment options from the last paid search yet."

    if any(term in normalized for term in ["link", "url", "book", "first one", "second one", "third one"]):
        return (
            f"🔗 **{selected.provider_name}**\n"
            f"- Booking/check link: {selected.booking_url}\n"
            f"- Phone: {selected.phone or 'not published'}\n"
            "Final appointment confirmation happens on the booking page or with the provider."
        )

    if "closest" in normalized or "nearest" in normalized:
        return (
            f"📍 **Closest-looking option from the last search**\n\n"
            f"- Provider: {selected.provider_name}\n"
            f"- Where: {selected.location}\n"
            f"- Link: {selected.booking_url}\n\n"
            "I can only rank by the order returned from the live/public source unless exact distances are published."
        )

    if any(term in normalized for term in ["medicare", "insurance", "takes", "accepts"]):
        insurance = search.insurance or "your insurance"
        return (
            f"I could not verify accepted insurance from the public search unless the source explicitly listed it.\n\n"
            f"Best option to check {insurance}: {selected.provider_name}\n"
            f"Link: {selected.booking_url}\n"
            f"Phone: {selected.phone or 'not published'}\n\n"
            "Confirm insurance before booking because provider directories and booking pages can be out of date."
        )

    if any(term in normalized for term in ["daughter", "son", "caregiver", "tell", "send"]):
        return (
            f"👨‍👩‍👧 Caregiver update: I found a {search.specialty} option near {search.location}: "
            f"{selected.provider_name}. Availability: {selected.earliest_available}. "
            f"Cost: {selected.estimated_cost}. Book/check here: {selected.booking_url}. "
            "Please help confirm the slot, insurance, transportation, and any referral/order requirements."
        )

    if any(term in normalized for term in ["bring", "referral", "order"]):
        if search.specialty == "imaging center":
            return (
                "For an MRI/imaging appointment, bring ID, insurance card, the clinician order/referral if you have one, "
                "a medication list, symptom/injury notes, and any prior imaging. Many centers will not schedule MRI without an order."
            )
        return (
            "Bring ID, insurance card, medication list, symptom timeline, caregiver contact, and any prior records or imaging."
        )

    return None


def appointment_chat_response(ctx: Context, sender: str, text: str) -> str:
    normalized = " ".join(text.lower().split())
    if normalized in {"hi", "hello", "hey", "help", "what can you do", "what do you do"}:
        return _intro_message()

    existing_search = APPOINTMENT_CONTEXT_BY_SENDER.get(sender)
    if existing_search and (not is_appointment_intent(text) or _is_followup(text)):
        return _answer_followup(sender, text, existing_search)

    request = CareRequest(
        case_id=make_case_id("chat-appt"),
        user_id=sender,
        text=text,
        context=_context_from_chat_text(text),
    )
    if not is_appointment_intent(text):
        return (
            "🩺 I can help find and prepare appointment booking links. Try asking: "
            "`Find a primary care doctor near UCLA this week with Medicare.`"
        )

    quote = build_appointment_payment_quote(request)
    return format_appointment_payment_prompt(request, quote)


async def appointment_chat_handler(ctx: Context, sender: str, text: str) -> str:
    normalized = " ".join(text.lower().split())
    if normalized in {"hi", "hello", "hey", "help", "what can you do", "what do you do"}:
        return appointment_chat_response(ctx, sender, text)

    paid_fingerprint, paid_search = _load_paid_search(ctx, sender)
    existing_search = paid_search or APPOINTMENT_CONTEXT_BY_SENDER.get(sender)
    if existing_search and (not is_appointment_intent(text) or _is_followup(text)):
        return _answer_followup(sender, text, existing_search)

    request = CareRequest(
        case_id=make_case_id("chat-appt"),
        user_id=sender,
        text=text,
        context=_context_from_chat_text(text),
    )
    if not is_appointment_intent(text):
        return appointment_chat_response(ctx, sender, text)

    request_fingerprint = _request_fingerprint(request)
    if paid_search and paid_fingerprint == request_fingerprint:
        return format_appointment_search_preview(paid_search)

    pending_payment = _load_pending_by_sender(ctx, sender)
    if pending_payment:
        if _pending_requires_refresh(pending_payment, request_fingerprint):
            ctx.logger.info(f"{AGENT_NAME}: refreshing stale pending payment {pending_payment.quote.reference}")
            _remove_pending(ctx, pending_payment)
        else:
            await _send_appointment_payment_request(ctx, sender, pending_payment.quote)
            return _pending_payment_message(pending_payment)

    quote = build_appointment_payment_quote(request)
    pending = PendingAppointmentPayment(
        original_sender=sender,
        request=request,
        quote=quote,
        response_channel="chat",
        request_fingerprint=request_fingerprint,
        created_at=time.time(),
        request_version=PAYMENT_REQUEST_VERSION,
    )
    _store_pending(ctx, pending)
    await _send_appointment_payment_request(ctx, sender, quote)
    return format_appointment_payment_prompt(request, quote)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    if not is_appointment_intent(msg.text):
        await ctx.send(
            sender,
            CareResult(
                case_id=msg.case_id,
                agent_name=AGENT_NAME,
                status="unsupported_intent",
                summary="This specialist handles appointment search and booking handoff requests.",
                next_actions=["Ask for a doctor, clinic, specialty, location, urgency, and insurance preference."],
                timeline_events=["Unsupported appointment intent"],
            ),
        )
        return

    quote = build_appointment_payment_quote(msg)
    pending = PendingAppointmentPayment(
        original_sender=sender,
        request=msg,
        quote=quote,
        response_channel="care",
        request_fingerprint=_request_fingerprint(msg),
        created_at=time.time(),
        request_version=PAYMENT_REQUEST_VERSION,
    )
    _store_pending(ctx, pending)
    ctx.logger.info(f"{AGENT_NAME}: requesting {quote.amount} {quote.currency} for appointment search from {sender}")
    await _send_appointment_payment_request(ctx, sender, quote)


@payment_proto.on_message(CommitPayment)
async def handle_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    reference = msg.reference or ""
    pending_payment = None
    for candidate in [reference, msg.transaction_id]:
        if candidate:
            pending_payment = _load_pending_by_reference(ctx, candidate)
            if pending_payment is not None:
                break
    if pending_payment is None:
        pending_payment = _load_pending_by_sender(ctx, sender)
    if pending_payment is not None:
        original_sender = pending_payment.original_sender
        request = pending_payment.request
        search = build_appointment_search_quote(request, pending_payment.quote)
        _store_paid_search(ctx, original_sender, pending_payment.request_fingerprint, search)
        result = appointment_paid_result(request, search)
        _remove_pending(ctx, pending_payment)
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id or pending_payment.quote.reference))
        if pending_payment.response_channel == "chat":
            await ctx.send(original_sender, create_text_chat(format_appointment_search_preview(search)))
        else:
            await ctx.send(original_sender, result)
        ctx.logger.info(f"{AGENT_NAME}: appointment search payment completed for {pending_payment.quote.reference}")
        return

    ctx.logger.warning(f"{AGENT_NAME}: unknown payment reference {reference or msg.transaction_id}")
    await ctx.send(
        sender,
        CancelPayment(
            transaction_id=msg.transaction_id,
            reason="Payment session not found or expired. Please send the appointment search again.",
        ),
    )


@payment_proto.on_message(RejectPayment)
async def handle_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    pending_payment = _load_pending_by_sender(ctx, sender)
    if pending_payment is not None:
        _remove_pending(ctx, pending_payment)
        request = pending_payment.request
        reason = msg.reason or "buyer rejected payment"
        unpaid = appointment_unpaid_result(request, reason)
        if pending_payment.response_channel == "chat":
            await ctx.send(sender, create_text_chat(unpaid.summary))
        else:
            await ctx.send(sender, unpaid)
        ctx.logger.info(f"{AGENT_NAME}: appointment search payment rejected for {pending_payment.quote.reference}: {reason}")
        return

    ctx.logger.warning(f"{AGENT_NAME}: reject from {sender} had no pending request")


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: received result from {sender}: {msg.status}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Appointment Assistant")


agent.include(create_chat_protocol(AGENT_NAME, appointment_chat_handler), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
