import json
import os
import re
from dataclasses import dataclass, field
from time import time
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
from doctor_office import (
    AGENT_NAME as DOCTOR_OFFICE_AGENT_NAME,
    book_doctor_office_appointment,
    is_doctor_office_booking_intent,
)
from domain import (
    APPOINTMENT_AGENT_NAME,
    APPOINTMENT_SERVICE_FEE_FET,
    PHARMACY_ASSISTANT_AGENT_NAME,
    OTC_ORDER_SERVICE_FEE_FET,
    build_adherence_plan,
    build_appointment_payment_quote,
    build_appointment_search_quote,
    build_otc_order_quote,
    build_otc_service_payment_quote,
    explain_prescription,
    format_appointment_search_preview,
    format_otc_order_preview,
    make_case_id,
    notify_caregiver,
    orchestrate_care,
    triage_emergency_reason,
    triage_request,
    triage_route,
)
from email_delivery import GmailDeliveryError, default_caregiver_email, gmail_missing_env, send_gmail_message
from llm import asi_chat_completion
from models import AppointmentSearchQuote, CareRequest, CareResult, PaymentQuote, PharmacyOrderQuote


AGENT_NAME = "careloop-orchestrator"
PORT = env_int("ORCHESTRATOR_AGENT_PORT", 8010)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="ORCHESTRATOR_AGENT_SEED",
    default_seed="careloop orchestrator seed phrase change me",
    description=(
        "CareLoop orchestrator coordinates triage, specialist routing, paid FET handoffs, "
        "and a visible care timeline."
    ),
    readme_path="agents/readmes/orchestrator.md",
)

care_proto = Protocol(name="CareLoopOrchestratorProtocol", version="0.3.0")
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")
ORCHESTRATOR_CONTEXT_BY_SENDER: dict[str, "OrchestratorSession"] = {}
MAX_ORCHESTRATOR_CONTEXTS = 100
PAYMENT_REQUEST_DEADLINE_SECONDS = 300
PAYMENT_EXPIRY_SECONDS = PAYMENT_REQUEST_DEADLINE_SECONDS - 15
PAYMENT_REQUEST_VERSION = "orchestrator-standard-payment-v3"
ALLOWED_ROUTES = {
    "careloop-prescription-explainer",
    PHARMACY_ASSISTANT_AGENT_NAME,
    APPOINTMENT_AGENT_NAME,
    DOCTOR_OFFICE_AGENT_NAME,
    "careloop-caregiver-notifier",
    "careloop-adherence",
    "careloop-orchestrator",
    "clarify",
}
DOCTOR_OFFICE_AGENT_ADDRESS = os.getenv("DOCTOR_OFFICE_AGENT_ADDRESS") or ""


@dataclass
class OrchestratorSession:
    case_id: str
    last_route: str = "clarify"
    last_text: str = ""
    last_paid_route: str | None = None
    last_paid_fingerprint: str | None = None
    last_appointment_search: AppointmentSearchQuote | None = None
    last_otc_order: PharmacyOrderQuote | None = None
    last_caregiver_channel: str | None = None
    last_caregiver_to_email: str | None = None
    last_caregiver_subject: str | None = None
    last_caregiver_body: str | None = None
    pending_doctor_booking_text: str | None = None
    pending_pharmacy_request_text: str | None = None
    completed_payment_references: set[str] = field(default_factory=set)
    timeline: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time)


@dataclass
class PendingOrchestratorPayment:
    original_sender: str
    request: CareRequest
    route: str
    quote: PaymentQuote
    request_fingerprint: str
    created_at: float
    request_version: str


pending_payments: dict[str, PendingOrchestratorPayment] = {}
pending_by_sender: dict[str, str] = {}
pending_doctor_bookings_by_case: dict[str, str] = {}


def _session(sender: str) -> OrchestratorSession:
    existing = ORCHESTRATOR_CONTEXT_BY_SENDER.get(sender)
    if existing:
        return existing
    if len(ORCHESTRATOR_CONTEXT_BY_SENDER) >= MAX_ORCHESTRATOR_CONTEXTS:
        oldest_sender = next(iter(ORCHESTRATOR_CONTEXT_BY_SENDER))
        ORCHESTRATOR_CONTEXT_BY_SENDER.pop(oldest_sender, None)
    created = OrchestratorSession(case_id=make_case_id("careloop"))
    ORCHESTRATOR_CONTEXT_BY_SENDER[sender] = created
    return created


def _add_timeline(session: OrchestratorSession, event: str) -> None:
    if session.timeline and session.timeline[-1] == event:
        return
    session.timeline.append(event)


def _model_dump(model) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)


def _request_fingerprint(request: CareRequest, route: str) -> str:
    normalized = " ".join(request.text.lower().split())
    return f"{route}|{normalized}"


def _pending_key(reference: str) -> str:
    return f"orchestrator:pending:{reference}"


def _pending_by_sender_key(sender: str) -> str:
    return f"orchestrator:pending-by-sender:{sender}"


def _pending_to_dict(pending: PendingOrchestratorPayment) -> dict[str, Any]:
    return {
        "original_sender": pending.original_sender,
        "request": _model_dump(pending.request),
        "route": pending.route,
        "quote": _model_dump(pending.quote),
        "request_fingerprint": pending.request_fingerprint,
        "created_at": pending.created_at,
        "request_version": pending.request_version,
    }


def _pending_from_dict(data: dict[str, Any]) -> PendingOrchestratorPayment:
    return PendingOrchestratorPayment(
        original_sender=str(data["original_sender"]),
        request=CareRequest(**data["request"]),
        route=str(data["route"]),
        quote=PaymentQuote(**data["quote"]),
        request_fingerprint=str(data["request_fingerprint"]),
        created_at=float(data["created_at"]),
        request_version=str(data.get("request_version") or "legacy"),
    )


def _is_pending_expired(pending: PendingOrchestratorPayment) -> bool:
    return time() - pending.created_at > PAYMENT_EXPIRY_SECONDS


def _store_pending(ctx: Context | None, pending: PendingOrchestratorPayment) -> None:
    old_reference = pending_by_sender.pop(pending.original_sender, None)
    if old_reference:
        pending_payments.pop(old_reference, None)
        if ctx is not None:
            ctx.storage.remove(_pending_key(old_reference))

    pending_payments[pending.quote.reference] = pending
    pending_by_sender[pending.original_sender] = pending.quote.reference
    if ctx is not None:
        ctx.storage.set(_pending_key(pending.quote.reference), _pending_to_dict(pending))
        ctx.storage.set(_pending_by_sender_key(pending.original_sender), pending.quote.reference)


def _remove_pending(ctx: Context | None, pending: PendingOrchestratorPayment) -> None:
    pending_payments.pop(pending.quote.reference, None)
    if pending_by_sender.get(pending.original_sender) == pending.quote.reference:
        pending_by_sender.pop(pending.original_sender, None)
    if ctx is not None:
        ctx.storage.remove(_pending_key(pending.quote.reference))
        if ctx.storage.get(_pending_by_sender_key(pending.original_sender)) == pending.quote.reference:
            ctx.storage.remove(_pending_by_sender_key(pending.original_sender))


def _load_pending_by_reference(ctx: Context | None, reference: str) -> PendingOrchestratorPayment | None:
    pending = pending_payments.get(reference)
    if pending is None and ctx is not None:
        data = ctx.storage.get(_pending_key(reference))
        if data:
            pending = _pending_from_dict(data)
            pending_payments[reference] = pending
            pending_by_sender[pending.original_sender] = reference
    if pending and _is_pending_expired(pending):
        _remove_pending(ctx, pending)
        return None
    return pending


def _load_pending_by_sender(ctx: Context | None, sender: str) -> PendingOrchestratorPayment | None:
    reference = pending_by_sender.get(sender)
    if reference is None and ctx is not None:
        reference = ctx.storage.get(_pending_by_sender_key(sender))
        if reference:
            pending_by_sender[sender] = reference
    if not reference:
        return None
    return _load_pending_by_reference(ctx, reference)


def _pending_requires_refresh(pending: PendingOrchestratorPayment, fingerprint: str) -> bool:
    expected_amount = APPOINTMENT_SERVICE_FEE_FET if pending.route == APPOINTMENT_AGENT_NAME else OTC_ORDER_SERVICE_FEE_FET
    return (
        pending.request_fingerprint != fingerprint
        or pending.request_version != PAYMENT_REQUEST_VERSION
        or pending.quote.amount != expected_amount
        or pending.quote.currency != "FET"
        or pending.quote.payment_method != "fet_direct"
    )


def _is_greeting_or_help(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if normalized.startswith("@"):
        parts = normalized.split(maxsplit=1)
        normalized = parts[1] if len(parts) > 1 else ""
    return normalized in {"hi", "hello", "hey", "help", "what can you do", "what do you do"}


def _is_timeline_request(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    return any(term in normalized for term in ["timeline", "status", "what happened", "summary so far"])


def _message_text(text: str) -> str:
    normalized = " ".join(text.split())
    if normalized.startswith("@"):
        parts = normalized.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return normalized


def _is_caregiver_message_request(text: str) -> bool:
    normalized = _message_text(text).lower()
    recipient_terms = [
        "caregiver",
        "caretaker",
        "care taker",
        "carer",
        "daughter",
        "son",
        "wife",
        "husband",
        "sister",
        "brother",
        "mom",
        "dad",
        "family",
    ]
    message_terms = [
        "write",
        "draft",
        "message",
        "text",
        "tell",
        "notify",
        "send",
        "let her know",
        "let him know",
        "let them know",
    ]
    has_message_action = any(term in normalized for term in message_terms)
    if not has_message_action:
        return False
    if any(term in normalized for term in recipient_terms):
        return True
    return any(term in normalized for term in ["write an email", "write a mail", "draft an email", "email for me"])


def _is_send_caregiver_email_request(text: str) -> bool:
    normalized = _message_text(text).lower()
    send_terms = ["send it", "send the email", "send this email", "email it", "send now", "yes send"]
    return any(term in normalized for term in send_terms)


def _is_doctor_offer_confirmation(session: OrchestratorSession, text: str) -> bool:
    if not session.pending_doctor_booking_text:
        return False
    normalized = _message_text(text).lower()
    confirmation_terms = [
        "yes",
        "yes please",
        "please proceed",
        "proceed",
        "go ahead",
        "book it",
        "schedule it",
        "confirm",
        "do it",
    ]
    return any(term == normalized or term in normalized for term in confirmation_terms)


def _is_pharmacy_offer_confirmation(session: OrchestratorSession, text: str) -> bool:
    if not session.pending_pharmacy_request_text:
        return False
    normalized = _message_text(text).lower()
    confirmation_terms = [
        "yes",
        "yes please",
        "please proceed",
        "proceed",
        "go ahead",
        "do it",
        "order it",
        "compare prices",
        "compare",
        "confirm",
        "start",
    ]
    return any(term == normalized or term in normalized for term in confirmation_terms)


def _is_result_followup(text: str) -> bool:
    normalized = _message_text(text).lower()
    followup_terms = [
        "closest",
        "nearest",
        "location",
        "where",
        "which one",
        "best one",
        "first one",
        "address",
        "phone",
        "call",
        "book",
        "booking link",
        "link",
        "cost",
        "price",
        "hours",
        "pickup",
        "delivery",
    ]
    return any(term in normalized for term in followup_terms)


def _is_generic_saved_result_followup(text: str) -> bool:
    normalized = _message_text(text).lower()
    if not _is_result_followup(text):
        return False
    words = normalized.split()
    if len(words) > 8:
        return False
    new_intent_terms = [
        "tylenol",
        "acetaminophen",
        "advil",
        "ibuprofen",
        "claritin",
        "loratadine",
        "tums",
        "benadryl",
        "otc",
        "over the counter",
        "medicine",
        "medication",
        "pharmacy",
        "prescription",
        "rx",
        "doctor",
        "clinic",
        "provider",
        "mri",
        "scan",
        "imaging",
        "radiology",
        "caregiver",
        "caretaker",
        "care taker",
        "carer",
        "daughter",
        "son",
        "reminder",
        "dose",
    ]
    return not any(term in normalized for term in new_intent_terms)


def _should_answer_saved_followup_before_llm(text: str) -> bool:
    normalized = _message_text(text).lower()
    words = normalized.split()
    if len(words) > 8:
        return False
    saved_followup_phrases = [
        "closest",
        "nearest",
        "closest location",
        "nearest location",
        "which one",
        "best one",
        "first one",
        "address",
        "phone",
        "link",
        "booking link",
        "cost",
        "price",
    ]
    return any(phrase in normalized for phrase in saved_followup_phrases)


def _direct_current_intent(text: str) -> dict[str, str] | None:
    clean_text = _message_text(text)
    if is_doctor_office_booking_intent(clean_text):
        return {
            "route": DOCTOR_OFFICE_AGENT_NAME,
            "confidence": "high",
            "rationale": "The user may want a simple primary-care doctor appointment that the Agentverse doctor office can book.",
        }

    decision = triage_route(clean_text)
    route = decision["route"]
    if route == "clarify":
        return None
    if route == APPOINTMENT_AGENT_NAME and _is_generic_saved_result_followup(text):
        return None
    return decision


def _llm_triage_decision(sender: str, text: str, session: OrchestratorSession) -> dict[str, str] | None:
    prompt = (
        "You are CareLoop's intent router. Classify the current user message into exactly one route. "
        "Prefer the current message over old context unless the current message is clearly a follow-up. "
        "Routes: careloop-prescription-explainer, careloop-pharmacy-assistant, "
        "careloop-appointment-assistant, careloop-doctor-office, careloop-caregiver-notifier, careloop-adherence, "
        "careloop-orchestrator, clarify. "
        "Use careloop-pharmacy-assistant for OTC medicine search/order/price/location questions, "
        "including Tylenol, Advil, Claritin, allergy medicine, pain medicine, pharmacy, or where to buy medicine. "
        "Use careloop-doctor-office when the user wants to book a normal doctor or primary-care appointment "
        "for cough, fever, cold, flu, sore throat, or a simple sick visit. "
        "Use careloop-appointment-assistant for open-ended provider searches, specialists, MRI, imaging, scans, "
        "or finding external booking links. "
        "Use careloop-caregiver-notifier for drafting/telling/texting/emailing a family member, caretaker, or caregiver. "
        "If the user asks to write an email or message about their current situation, use careloop-caregiver-notifier. "
        "Return only compact JSON with keys route, confidence, rationale."
    )
    context = {
        "current_message": _message_text(text),
        "last_route": session.last_route,
        "last_user_text": session.last_text,
        "has_saved_appointment_result": session.last_appointment_search is not None,
        "has_saved_otc_result": session.last_otc_order is not None,
    }
    content = asi_chat_completion(
        system_prompt=prompt,
        user_prompt=json.dumps(context),
        session_id=f"careloop-orchestrator-triage-{sender}",
        max_tokens=160,
        temperature=0,
    )
    if not content:
        return None
    try:
        start = content.find("{")
        end = content.rfind("}")
        payload = json.loads(content[start : end + 1] if start >= 0 and end >= start else content)
    except Exception:
        return None
    route = str(payload.get("route", "")).strip()
    if route not in ALLOWED_ROUTES or route == "clarify":
        return None
    return {
        "route": route,
        "confidence": str(payload.get("confidence") or "medium"),
        "rationale": str(payload.get("rationale") or "ASI:One classified the current user intent."),
    }


def _current_intent_decision(
    sender: str,
    text: str,
    session: OrchestratorSession,
    *,
    use_llm: bool,
) -> dict[str, str] | None:
    if use_llm:
        llm_decision = _llm_triage_decision(sender, text, session)
        if llm_decision:
            return llm_decision
    return _direct_current_intent(text)


def _is_short_followup(text: str) -> bool:
    if _is_caregiver_message_request(text):
        return False
    normalized = _message_text(text).lower()
    return len(normalized.split()) <= 10 and any(
        term in normalized
        for term in [
            "yes",
            "near",
            "usc",
            "ucla",
            "westwood",
            "medicare",
            "delivery",
            "pickup",
            "daughter",
            "son",
            "shorter",
            "link",
            "ready",
            "closest",
            "nearest",
            "location",
            "where",
            "address",
            "phone",
        ]
    )


def _intro_message() -> str:
    return (
        "👋 **Hi, I’m CareLoop.** Tell me what is happening and I’ll guide the next step.\n\n"
        "I can help with:\n"
        "- 🧾 Prescription questions and scanned labels\n"
        "- 💊 OTC medicine search, price comparison, and checkout handoff\n"
        "- 🩺 Appointment searches and Agentverse doctor booking\n"
        "- 👨‍👩‍👧 Caregiver messages and Gmail sending\n"
        "- ⏰ Medication reminder planning"
    )


def _format_timeline(session: OrchestratorSession) -> str:
    if not session.timeline:
        return "📍 **CareLoop timeline**\n\nNo care steps yet."
    compact_events: list[str] = []
    for event in session.timeline:
        if event not in compact_events:
            compact_events.append(event)
    lines = "\n".join(f"{index}. {event}" for index, event in enumerate(compact_events[-8:], start=1))
    return f"📍 **CareLoop timeline**\n**Case:** `{session.case_id}`\n\n{lines}"


def _specialist_handle(route: str) -> str:
    if route == "careloop-prescription-explainer":
        return "@careloop-prescription-explainer"
    if route == PHARMACY_ASSISTANT_AGENT_NAME:
        return "@careloop-pharmacy-options"
    if route == APPOINTMENT_AGENT_NAME:
        return "@careloop-appointment-assistant"
    if route == DOCTOR_OFFICE_AGENT_NAME:
        return "@careloop-doctor-office"
    if route == "careloop-caregiver-notifier":
        return "@careloop-caregiver-notifier"
    if route == "careloop-adherence":
        return "@careloop-adherence"
    return f"@{route}"


def _paid_handoff(route: str, text: str, session: OrchestratorSession, reason: str) -> str:
    quote = _build_paid_quote(route, CareRequest(case_id=session.case_id, user_id="preview", text=text))
    _add_timeline(session, "Payment requested")
    return _format_paid_payment_prompt(route, CareRequest(case_id=session.case_id, user_id="preview", text=text), quote, session)


def _build_paid_quote(route: str, request: CareRequest) -> PaymentQuote:
    if route == APPOINTMENT_AGENT_NAME:
        return build_appointment_payment_quote(request)
    return build_otc_service_payment_quote(request)


def _format_paid_payment_prompt(route: str, request: CareRequest, quote: PaymentQuote, session: OrchestratorSession) -> str:
    if route == APPOINTMENT_AGENT_NAME:
        return (
            "🩺 **CareLoop can check nearby appointment and imaging options for you.**\n\n"
            f"💳 To start the live search, please approve the **{quote.amount} FET** CareLoop service fee "
            f"({quote.amount} FET CareLoop service fee).\n\n"
            "| After payment | What I’ll return |\n"
            "|---|---|\n"
            "| Provider search | Real providers or booking links I can verify |\n"
            "| Cost/availability | Public details when the source publishes them |\n"
            "| MRI note | Many centers require a clinician order or referral before scheduling |\n"
        )

    return (
        "💊 **CareLoop can compare over-the-counter medicine options and prices for you.**\n\n"
        f"💳 To start the live search, please approve the **{quote.amount} FET** CareLoop service fee "
        f"({quote.amount} FET CareLoop service fee).\n\n"
        "| After payment | What I’ll return |\n"
        "|---|---|\n"
        "| Online prices | Verified prices I can read |\n"
        "| Pickup options | Nearby real pharmacy locations |\n"
        "| Checkout handoff | Provider link for final purchase |\n"
    )


def _answer_from_appointment_context(text: str, search: AppointmentSearchQuote) -> str:
    option = search.selected_option or (search.options[0] if search.options else None)
    if option is None:
        return "I don’t have a saved appointment option from the last search yet."

    normalized = _message_text(text).lower()
    if any(term in normalized for term in ["closest", "nearest", "location", "where", "which one", "best one"]):
        details = [
            f"The closest/best option from the results I found is {option.provider_name}.",
            f"Location: {option.location}",
        ]
        if option.earliest_available and option.earliest_available != "availability not published":
            details.append(f"Availability: {option.earliest_available}")
        if option.phone:
            details.append(f"Phone: {option.phone}")
        details.append(f"Booking link: {option.booking_url}")
        if option.notes:
            details.append(f"Note: {option.notes}")
        return "📍 **Closest saved option**\n\n" + "\n".join(f"- {detail}" for detail in details)

    if "phone" in normalized or "call" in normalized:
        return (
            f"☎️ **{option.provider_name}**\n"
            f"- Phone: {option.phone or 'not published'}\n"
            f"- Booking link: {option.booking_url}"
        )

    if "link" in normalized or "book" in normalized:
        return f"🔗 **Booking/search link for {option.provider_name}:**\n{option.booking_url}"

    if "cost" in normalized or "price" in normalized:
        return (
            f"💵 **{option.provider_name} cost info:** {option.estimated_cost}\n\n"
            "Confirm final cost and insurance coverage with the provider."
        )

    return (
        f"From the saved search, I’d start with {option.provider_name} at {option.location}. "
        f"Booking link: {option.booking_url}"
    )


def _answer_from_otc_context(text: str, order: PharmacyOrderQuote) -> str:
    normalized = _message_text(text).lower()
    if any(term in normalized for term in ["closest", "nearest", "location", "pickup", "where"]):
        first = (order.nearby_pharmacies or [None])[0]
        if first:
            return (
                "📍 **Closest pickup option from the saved search**\n\n"
                f"{first}\n\n"
                "Please confirm shelf availability with the store before going."
            )
        return "I don’t have a saved closest pickup location from the last search yet."
    if "price" in normalized or "cost" in normalized:
        return f"💵 The saved online quote was **{order.subtotal_usd}** for **{order.product.name}** from {order.product.price_source}."
    return format_otc_order_preview(order)


def _answer_saved_followup(session: OrchestratorSession, text: str) -> str | None:
    if session.last_appointment_search is not None and (
        session.last_paid_route == APPOINTMENT_AGENT_NAME or _is_result_followup(text)
    ):
        return _answer_from_appointment_context(text, session.last_appointment_search)
    if session.last_otc_order is not None and (
        session.last_paid_route == PHARMACY_ASSISTANT_AGENT_NAME or _is_result_followup(text)
    ):
        return _answer_from_otc_context(text, session.last_otc_order)
    return None


def _caregiver_context_text(session: OrchestratorSession, text: str) -> str:
    base = _message_text(text)
    if session.last_appointment_search is not None:
        option = session.last_appointment_search.selected_option or (
            session.last_appointment_search.options[0] if session.last_appointment_search.options else None
        )
        if option is not None:
            return (
                f"{base}\n\n"
                f"Current appointment option: {option.provider_name} at {option.location}. "
                f"Booking link: {option.booking_url}. "
                "Status: patient is reviewing or preparing to book."
            )
    if session.last_otc_order is not None:
        order = session.last_otc_order
        return (
            f"{base}\n\n"
            f"Current pharmacy option: {order.product.name} {order.product.strength} from {order.product.provider}. "
            f"Estimated total: {order.total_usd}. Checkout link: {order.checkout_url}."
        )
    return base


def _saved_care_context(session: OrchestratorSession) -> str:
    if session.last_appointment_search is not None:
        option = session.last_appointment_search.selected_option or (
            session.last_appointment_search.options[0] if session.last_appointment_search.options else None
        )
        if option is not None:
            parts = [
                f"Appointment option: {option.provider_name}",
                f"Location: {option.location}",
                f"Booking link: {option.booking_url}",
                "Status: patient is reviewing or preparing to book unless the user says it is already booked.",
            ]
            if option.earliest_available and option.earliest_available != "availability not published":
                parts.append(f"Availability: {option.earliest_available}")
            if option.phone:
                parts.append(f"Phone: {option.phone}")
            return "\n".join(parts)
    if session.last_otc_order is not None:
        order = session.last_otc_order
        return (
            f"Pharmacy option: {order.product.name} {order.product.strength} from {order.product.provider}\n"
            f"Estimated total: {order.total_usd}\n"
            f"Checkout link: {order.checkout_url}"
        )
    return "No saved appointment or pharmacy result."


def _format_caregiver_draft(result: CareResult) -> str:
    draft = result.summary.split("\n\n", 1)[1] if "\n\n" in result.summary else result.summary
    return f"👨‍👩‍👧 **Here’s a caregiver message you can send:**\n\n{draft}"


def _fallback_caregiver_draft(text: str, result: CareResult, session: OrchestratorSession) -> str:
    normalized = _message_text(text)
    lower = normalized.lower()
    if "daughter" in lower:
        greeting = "Hi, just wanted to let you know"
    elif "son" in lower:
        greeting = "Hi, just wanted to let you know"
    else:
        greeting = "Hi, I wanted to share a quick CareLoop update"

    fact_text = normalized
    for marker in ["saying that", "that i", "that I"]:
        index = fact_text.find(marker)
        if index >= 0:
            fact_text = fact_text[index + len(marker) :].strip()
            if marker.lower().endswith("i"):
                fact_text = "I " + fact_text
            break
    fact_text = fact_text.rstrip(".")
    if "appointment" in lower and session.last_appointment_search is not None:
        option = session.last_appointment_search.selected_option or (
            session.last_appointment_search.options[0] if session.last_appointment_search.options else None
        )
        if option is not None and option.provider_name.lower() not in fact_text.lower():
            fact_text = f"{fact_text} at {option.provider_name}"
    if fact_text:
        return f"👨‍👩‍👧 **Here’s a caregiver message you can send:**\n\n{greeting} that {fact_text}. Please check in when you can."
    return _format_caregiver_draft(result)


def _caregiver_channel(text: str) -> str:
    normalized = _message_text(text).lower()
    if any(term in normalized for term in ["email", "mail", "gmail"]):
        return "email"
    return "sms"


def _caregiver_to_email(text: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", _message_text(text))
    return match.group(0) if match else default_caregiver_email()


def _parse_email_draft(raw: str, fallback_subject: str) -> tuple[str, str]:
    subject = fallback_subject
    body_lines: list[str] = []
    in_body = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if body_lines:
                body_lines.append("")
            continue
        if stripped.lower().startswith("subject:"):
            subject = stripped.split(":", 1)[1].strip() or fallback_subject
            continue
        if stripped.lower().startswith("body:"):
            in_body = True
            possible_body = stripped.split(":", 1)[1].strip()
            if possible_body:
                body_lines.append(possible_body)
            continue
        if in_body or not stripped.lower().startswith(("to:", "from:")):
            body_lines.append(stripped)
    body = "\n".join(body_lines).strip() or raw.strip()
    return subject, body


def _remember_caregiver_draft(
    session: OrchestratorSession,
    *,
    channel: str,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    session.last_caregiver_channel = channel
    session.last_caregiver_to_email = to_email
    session.last_caregiver_subject = subject
    session.last_caregiver_body = body


def _smart_caregiver_draft(sender: str, session: OrchestratorSession, text: str, result: CareResult) -> str:
    channel = _caregiver_channel(text)
    to_email = _caregiver_to_email(text)
    fallback = _fallback_caregiver_draft(text, result, session).split("\n\n", 1)[-1]
    fallback_subject = "CareLoop update"
    llm_answer = asi_chat_completion(
        system_prompt=(
            "You write warm, concise caregiver-ready messages for older adults. "
            "Do not copy the user's instruction. Do not say 'the patient' unless the user used that wording. "
            "If the user says 'I', write from the user's point of view. "
            "Use only facts provided in the user request or saved care context. "
            "Do not invent medical advice, appointment times, confirmations, or diagnoses. "
            "If writing an email, return exactly a Subject line and a Body section. Otherwise write a short text message. "
            "Return only the message the caregiver should receive."
        ),
        user_prompt=(
            f"Channel: {channel}\n\n"
            f"User request:\n{_message_text(text)}\n\n"
            f"Saved care context:\n{_saved_care_context(session)}\n\n"
            f"Structured fallback draft:\n{result.summary}\n\n"
            "Write the final caregiver message now."
        ),
        session_id=f"careloop-orchestrator-caregiver-{sender}",
        max_tokens=260,
        temperature=0.2,
    )
    draft = (llm_answer or fallback).strip()
    if channel == "email":
        subject, body = _parse_email_draft(draft, fallback_subject)
        _remember_caregiver_draft(
            session,
            channel="email",
            to_email=to_email,
            subject=subject,
            body=body,
        )
        return (
            f"✉️ **Drafted email to {to_email}**\n\n"
            f"**Subject:** {subject}\n\n"
            f"{body}\n\n"
            "Say `send it` to send this with Gmail."
        )

    _remember_caregiver_draft(
        session,
        channel="sms",
        to_email=to_email,
        subject=fallback_subject,
        body=draft,
    )
    return f"👨‍👩‍👧 **Here’s a caregiver message you can send:**\n\n{draft}"


def _send_saved_caregiver_email(session: OrchestratorSession) -> str:
    if not session.last_caregiver_body:
        return "I don’t have a caregiver email draft ready yet. Ask me to write the email first."
    if session.last_caregiver_channel != "email":
        return "I have a text-message draft saved, not an email draft. Ask me to write it as an email first."

    missing = gmail_missing_env()
    if missing:
        return (
            "I drafted the email, but Gmail sending is not configured yet.\n\n"
            f"Missing env values: {', '.join(missing)}"
        )

    try:
        sent = send_gmail_message(
            to_email=session.last_caregiver_to_email or default_caregiver_email(),
            subject=session.last_caregiver_subject or "CareLoop update",
            body=session.last_caregiver_body,
        )
    except GmailDeliveryError as exc:
        return f"I could not send the Gmail message yet: {exc}"

    _add_timeline(session, f"Caregiver email sent to {sent.to_email}")
    return (
        f"✅ **Sent the caregiver email to {sent.to_email}.**\n\n"
        f"**Subject:** {sent.subject}\n"
        f"**Gmail message id:** {sent.message_id or 'sent'}"
    )


def _payment_card_content(route: str, quote: PaymentQuote) -> str:
    if route == APPOINTMENT_AGENT_NAME:
        return (
            "Please complete the FET payment to run the live appointment search. "
            "After payment, CareLoop will look up real providers and booking links."
        )
    return (
        "Please complete the FET payment to run the live OTC pharmacy price comparison. "
        "After payment, CareLoop will compare online and pickup options."
    )


async def _send_payment_request(ctx: Context, sender: str, route: str, quote: PaymentQuote) -> None:
    use_testnet = os.getenv("FET_USE_TESTNET", "true").lower() == "true"
    agent_wallet_address = ""
    try:
        agent_wallet_address = str(agent.wallet.address())
    except Exception:
        agent_wallet_address = ""
    recipient = agent_wallet_address or str(ctx.agent.address)
    service = "careloop_appointment_search" if route == APPOINTMENT_AGENT_NAME else "careloop_otc_pharmacy_search"
    metadata: dict[str, str] = {
        "agent": AGENT_NAME,
        "service": service,
        "fet_network": "stable-testnet" if use_testnet else "mainnet",
        "mainnet": "false" if use_testnet else "true",
        "content": _payment_card_content(route, quote),
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
    ctx.logger.info(f"{AGENT_NAME}: sending FET payment request to {sender}: {payment_request}")
    await ctx.send(sender, payment_request)


async def _begin_paid_work(
    ctx: Context,
    sender: str,
    route: str,
    request: CareRequest,
    session: OrchestratorSession,
) -> str | None:
    fingerprint = _request_fingerprint(request, route)
    if (
        session.last_paid_route == route
        and session.last_paid_fingerprint == fingerprint
        and session.last_appointment_search is not None
        and route == APPOINTMENT_AGENT_NAME
    ):
        return format_appointment_search_preview(session.last_appointment_search)
    if (
        session.last_paid_route == route
        and session.last_paid_fingerprint == fingerprint
        and session.last_otc_order is not None
        and route == PHARMACY_ASSISTANT_AGENT_NAME
    ):
        return format_otc_order_preview(session.last_otc_order)

    pending = _load_pending_by_sender(ctx, sender)
    if pending:
        if _pending_requires_refresh(pending, fingerprint):
            ctx.logger.info(f"{AGENT_NAME}: refreshing stale pending payment {pending.quote.reference}")
            _remove_pending(ctx, pending)
        else:
            await _send_payment_request(ctx, sender, route, pending.quote)
            return _format_paid_payment_prompt(route, request, pending.quote, session)

    quote = _build_paid_quote(route, request)
    pending = PendingOrchestratorPayment(
        original_sender=sender,
        request=request,
        route=route,
        quote=quote,
        request_fingerprint=fingerprint,
        created_at=time(),
        request_version=PAYMENT_REQUEST_VERSION,
    )
    _store_pending(ctx, pending)
    _add_timeline(session, "Payment requested")
    await _send_payment_request(ctx, sender, route, quote)
    return _format_paid_payment_prompt(route, request, quote, session)


def _local_result(route: str, request: CareRequest) -> CareResult:
    if route == "careloop-prescription-explainer":
        return explain_prescription(request)
    if route == "careloop-caregiver-notifier":
        return notify_caregiver(request)
    if route == "careloop-adherence":
        return build_adherence_plan(request)
    if route == DOCTOR_OFFICE_AGENT_NAME:
        return book_doctor_office_appointment(request)
    return triage_request(request)


def _format_direct_booking_result(session: OrchestratorSession, result: CareResult) -> str:
    for event in result.timeline_events or []:
        _add_timeline(session, event)
    _add_timeline(session, "Doctor appointment confirmed")
    session.last_route = result.agent_name
    session.last_text = result.summary
    return (
        f"{result.summary}\n\n"
        "👨‍👩‍👧 I can also write or send a caregiver email about this appointment."
    )


def _doctor_office_offer(session: OrchestratorSession, route_text: str) -> str:
    session.pending_doctor_booking_text = route_text
    session.pending_pharmacy_request_text = None
    session.last_route = DOCTOR_OFFICE_AGENT_NAME
    session.last_text = route_text
    _add_timeline(session, "Agentverse doctor booking option offered")
    return (
        "🩺 **I found an Agentverse doctor who can book this end to end.**\n\n"
        "| Doctor office | Details |\n"
        "|---|---|\n"
        "| Specialist | CareLoop Doctor Office |\n"
        "| Doctor | Dr. Maya Patel |\n"
        "| Clinic | CareLoop Family Clinic near USC Village |\n"
        "| Can do | Create the appointment and send the Google Calendar invite |\n\n"
        "**Would you like me to proceed?**"
    )


def _pharmacy_offer(session: OrchestratorSession, route_text: str) -> str:
    session.pending_pharmacy_request_text = route_text
    session.pending_doctor_booking_text = None
    session.last_route = PHARMACY_ASSISTANT_AGENT_NAME
    session.last_text = route_text
    _add_timeline(session, "Agentverse pharmacy assistant offered")
    return (
        "💊 **I found an Agentverse pharmacy assistant that can handle this.**\n\n"
        "| Capability | What it does |\n"
        "|---|---|\n"
        "| OTC search | Compares medicine options and prices |\n"
        "| Fulfillment | Shows pickup or delivery options |\n"
        "| Checkout | Prepares checkout after a small FET service fee |\n\n"
        "**Would you like me to proceed?**"
    )


async def _begin_pharmacy_paid_work(
    ctx: Context,
    sender: str,
    session: OrchestratorSession,
) -> str | None:
    pending_text = session.pending_pharmacy_request_text or session.last_text
    session.pending_pharmacy_request_text = None
    request = CareRequest(case_id=session.case_id, user_id=sender, text=pending_text)
    return await _begin_paid_work(ctx, sender, PHARMACY_ASSISTANT_AGENT_NAME, request, session)


def _begin_pharmacy_paid_work_preview(sender: str, session: OrchestratorSession) -> str:
    pending_text = session.pending_pharmacy_request_text or session.last_text
    session.pending_pharmacy_request_text = None
    session.last_route = PHARMACY_ASSISTANT_AGENT_NAME
    session.last_text = pending_text
    return _paid_handoff(
        PHARMACY_ASSISTANT_AGENT_NAME,
        pending_text,
        session,
        "user confirmed pharmacy offer",
    )


async def _book_doctor_office(
    ctx: Context | None,
    sender: str,
    session: OrchestratorSession,
    route_text: str,
) -> str | None:
    session.pending_doctor_booking_text = None
    request = CareRequest(case_id=session.case_id, user_id=sender, text=route_text)
    if ctx is not None and DOCTOR_OFFICE_AGENT_ADDRESS:
        pending_doctor_bookings_by_case[request.case_id] = sender
        _add_timeline(session, "Doctor office booking requested")
        await ctx.send(DOCTOR_OFFICE_AGENT_ADDRESS, request)
        return "✅ Great. I’m booking this with CareLoop Doctor Office now and will send the confirmed appointment here."
    return _format_direct_booking_result(session, book_doctor_office_appointment(request))


def _book_doctor_office_preview(sender: str, session: OrchestratorSession, route_text: str) -> str:
    session.pending_doctor_booking_text = None
    request = CareRequest(case_id=session.case_id, user_id=sender, text=route_text)
    return _format_direct_booking_result(session, book_doctor_office_appointment(request))


def _complete_paid_work(pending: PendingOrchestratorPayment, session: OrchestratorSession) -> str:
    request = pending.request
    if pending.route == APPOINTMENT_AGENT_NAME:
        search = build_appointment_search_quote(request, pending.quote)
        session.last_paid_route = pending.route
        session.last_paid_fingerprint = pending.request_fingerprint
        session.last_appointment_search = search
        session.last_otc_order = None
        _add_timeline(session, "Appointment search completed after FET payment")
        return (
            f"{format_appointment_search_preview(search)}\n\n"
            f"{_format_timeline(session)}"
        )

    order = build_otc_order_quote(request, pending.quote)
    session.last_paid_route = pending.route
    session.last_paid_fingerprint = pending.request_fingerprint
    session.last_otc_order = order
    session.last_appointment_search = None
    _add_timeline(session, "OTC pharmacy search completed after FET payment")
    return (
        f"{format_otc_order_preview(order)}\n\n"
        f"{_format_timeline(session)}"
    )


async def _route_decision(
    ctx: Context,
    sender: str,
    session: OrchestratorSession,
    route_text: str,
    decision: dict[str, str],
) -> str | None:
    route = decision["route"]
    session.last_route = route
    session.last_text = route_text
    if route != DOCTOR_OFFICE_AGENT_NAME:
        session.pending_doctor_booking_text = None
    if route != PHARMACY_ASSISTANT_AGENT_NAME:
        session.pending_pharmacy_request_text = None
    _add_timeline(session, f"Triage route: {route} ({decision['confidence']})")

    request = CareRequest(case_id=session.case_id, user_id=sender, text=route_text)
    if route == DOCTOR_OFFICE_AGENT_NAME:
        return _doctor_office_offer(session, route_text)

    if route == PHARMACY_ASSISTANT_AGENT_NAME:
        return _pharmacy_offer(session, route_text)

    if route == APPOINTMENT_AGENT_NAME:
        return await _begin_paid_work(ctx, sender, route, request, session)

    return _complete_local_route(session, route, request)


def _route_decision_preview(
    sender: str,
    session: OrchestratorSession,
    route_text: str,
    decision: dict[str, str],
) -> str:
    route = decision["route"]
    session.last_route = route
    session.last_text = route_text
    if route != DOCTOR_OFFICE_AGENT_NAME:
        session.pending_doctor_booking_text = None
    if route != PHARMACY_ASSISTANT_AGENT_NAME:
        session.pending_pharmacy_request_text = None
    _add_timeline(session, f"Triage route: {route} ({decision['confidence']})")

    request = CareRequest(case_id=session.case_id, user_id=sender, text=route_text)
    if route == DOCTOR_OFFICE_AGENT_NAME:
        return _doctor_office_offer(session, route_text)

    if route == PHARMACY_ASSISTANT_AGENT_NAME:
        return _pharmacy_offer(session, route_text)

    if route == APPOINTMENT_AGENT_NAME:
        return _paid_handoff(route, route_text, session, decision["rationale"])

    return _complete_local_route(session, route, request)


def _complete_local_route(session: OrchestratorSession, route: str, request: CareRequest) -> str:
    if route == "careloop-orchestrator":
        _add_timeline(session, "Prescription-readiness flow held for orchestrator context")
        return (
            "💊 CareLoop can coordinate prescription readiness, but the standalone prescription status connector is still mocked.\n\n"
            "For the demo, I’ll keep this as an orchestrator-owned timeline item instead of asking the older adult to know hidden e-prescription details."
        )

    if route == "clarify":
        _add_timeline(session, "Clarification requested")
        return (
            "I need one detail before coordinating this.\n\n"
            "Is this about a prescription, OTC medicine, an appointment, a caregiver update, or a medication reminder?"
        )

    result = _local_result(route, request)
    if route == DOCTOR_OFFICE_AGENT_NAME:
        return _format_direct_booking_result(session, result)

    for event in result.timeline_events or []:
        _add_timeline(session, event)
    next_actions = "\n".join(f"- {action}" for action in result.next_actions)
    return (
        f"✅ **CareLoop handled this with {_specialist_handle(route)}.**\n\n"
        f"{result.summary}\n\n"
        f"**Next actions**\n{next_actions}\n\n"
        f"{_format_timeline(session)}"
    )


async def _orchestrator_answer(ctx: Context | None, sender: str, text: str) -> str | None:
    session = _session(sender)
    if _is_greeting_or_help(text):
        return _intro_message()
    if _is_timeline_request(text):
        return _format_timeline(session)
    emergency_reason = triage_emergency_reason(text)
    if emergency_reason:
        _add_timeline(session, f"Emergency stop: {emergency_reason}")
        return (
            f"🚨 **This may be an emergency ({emergency_reason}). Call 911 or local emergency services now.**\n\n"
            "CareLoop should not automate this. Notify a caregiver immediately if you can do so without delaying care."
        )
    if _is_send_caregiver_email_request(text):
        return _send_saved_caregiver_email(session)
    if _is_doctor_offer_confirmation(session, text):
        return await _book_doctor_office(ctx, sender, session, session.pending_doctor_booking_text)
    if _is_pharmacy_offer_confirmation(session, text):
        return await _begin_pharmacy_paid_work(ctx, sender, session)
    if _is_caregiver_message_request(text):
        request = CareRequest(case_id=session.case_id, user_id=sender, text=_caregiver_context_text(session, text))
        result = notify_caregiver(request)
        session.last_route = "careloop-caregiver-notifier"
        session.last_text = _message_text(text)
        _add_timeline(session, "Triage route: careloop-caregiver-notifier (high)")
        for event in result.timeline_events or []:
            _add_timeline(session, event)
        return _smart_caregiver_draft(sender, session, text, result)
    if _should_answer_saved_followup_before_llm(text):
        saved_answer = _answer_saved_followup(session, text)
        if saved_answer:
            return saved_answer
    current_decision = _current_intent_decision(sender, text, session, use_llm=ctx is not None)
    if current_decision:
        if ctx is None:
            return _route_decision_preview(sender, session, _message_text(text), current_decision)
        return await _route_decision(ctx, sender, session, _message_text(text), current_decision)
    if _is_result_followup(text):
        saved_answer = _answer_saved_followup(session, text)
        if saved_answer:
            return saved_answer

    combined_text = f"{session.last_text}\nFollow-up detail: {text}" if session.last_text and _is_short_followup(text) else text
    decision = triage_route(combined_text)
    if ctx is None:
        return _route_decision_preview(sender, session, combined_text, decision)
    return await _route_decision(ctx, sender, session, combined_text, decision)


def _orchestrator_answer_preview(sender: str, text: str) -> str:
    session = _session(sender)
    if _is_greeting_or_help(text):
        return _intro_message()
    if _is_timeline_request(text):
        return _format_timeline(session)
    emergency_reason = triage_emergency_reason(text)
    if emergency_reason:
        _add_timeline(session, f"Emergency stop: {emergency_reason}")
        return (
            f"This may be an emergency ({emergency_reason}). Call 911 or local emergency services now.\n\n"
            "CareLoop should not automate this. Notify a caregiver immediately if you can do so without delaying care."
        )
    if _is_send_caregiver_email_request(text):
        return _send_saved_caregiver_email(session)
    if _is_doctor_offer_confirmation(session, text):
        return _book_doctor_office_preview(sender, session, session.pending_doctor_booking_text)
    if _is_pharmacy_offer_confirmation(session, text):
        return _begin_pharmacy_paid_work_preview(sender, session)
    if _is_caregiver_message_request(text):
        request = CareRequest(case_id=session.case_id, user_id=sender, text=_caregiver_context_text(session, text))
        result = notify_caregiver(request)
        session.last_route = "careloop-caregiver-notifier"
        session.last_text = _message_text(text)
        _add_timeline(session, "Triage route: careloop-caregiver-notifier (high)")
        for event in result.timeline_events or []:
            _add_timeline(session, event)
        return _smart_caregiver_draft(sender, session, text, result)
    if _should_answer_saved_followup_before_llm(text):
        saved_answer = _answer_saved_followup(session, text)
        if saved_answer:
            return saved_answer
    current_decision = _current_intent_decision(sender, text, session, use_llm=False)
    if current_decision:
        return _route_decision_preview(sender, session, _message_text(text), current_decision)
    if _is_result_followup(text):
        saved_answer = _answer_saved_followup(session, text)
        if saved_answer:
            return saved_answer

    combined_text = f"{session.last_text}\nFollow-up detail: {text}" if session.last_text and _is_short_followup(text) else text
    decision = triage_route(combined_text)
    return _route_decision_preview(sender, session, combined_text, decision)


def orchestrator_chat_response(ctx: Context, sender: str, text: str) -> str | None:
    if ctx is None:
        return _orchestrator_answer_preview(sender, text)
    return _orchestrator_answer(ctx, sender, text)


def telegram_pending_paid_quote(sender: str) -> tuple[str, CareRequest, PaymentQuote] | None:
    """If the orchestrator's last response to this Telegram sender was a paid handoff
    that has not been completed yet, return the (route, request, quote) the bridge
    should drive payment for. Returns None otherwise."""
    session = ORCHESTRATOR_CONTEXT_BY_SENDER.get(sender)
    if session is None:
        return None
    if session.last_route not in {APPOINTMENT_AGENT_NAME, PHARMACY_ASSISTANT_AGENT_NAME}:
        return None
    request_text = session.last_text or ""
    if not request_text:
        return None
    request = CareRequest(case_id=session.case_id, user_id=sender, text=request_text)
    fingerprint = _request_fingerprint(request, session.last_route)
    if (
        session.last_paid_route == session.last_route
        and session.last_paid_fingerprint == fingerprint
    ):
        return None
    quote = _build_paid_quote(session.last_route, request)
    return session.last_route, request, quote


def telegram_complete_paid_work(
    sender: str,
    route: str,
    request: CareRequest,
    quote: PaymentQuote,
    transaction_id: str,
) -> str:
    """Drive the orchestrator's paid-work completion from the Telegram bridge once
    the bridge has settled the FET payment on the Fetch.ai testnet."""
    session = _session(sender)
    fingerprint = _request_fingerprint(request, route)
    pending = PendingOrchestratorPayment(
        original_sender=sender,
        request=request,
        route=route,
        quote=quote,
        request_fingerprint=fingerprint,
        created_at=time(),
        request_version=PAYMENT_REQUEST_VERSION,
    )
    if quote.reference in session.completed_payment_references:
        if route == APPOINTMENT_AGENT_NAME and session.last_appointment_search is not None:
            return format_appointment_search_preview(session.last_appointment_search)
        if route == PHARMACY_ASSISTANT_AGENT_NAME and session.last_otc_order is not None:
            return format_otc_order_preview(session.last_otc_order)
    session.completed_payment_references.add(quote.reference)
    short_tx = (transaction_id or "telegram-fet")[:16]
    _add_timeline(
        session,
        f"FET payment completed via Telegram: {quote.amount} {quote.currency} (tx {short_tx})",
    )
    return _complete_paid_work(pending, session)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, orchestrate_care(msg))


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    original_sender = pending_doctor_bookings_by_case.pop(msg.case_id, None)
    if original_sender:
        session = _session(original_sender)
        _add_timeline(session, f"Specialist result received: {msg.agent_name} ({msg.status})")
        for event in msg.timeline_events or []:
            _add_timeline(session, event)
        await ctx.send(original_sender, create_text_chat(_format_direct_booking_result(session, msg)))
        ctx.logger.info(f"{AGENT_NAME}: forwarded doctor office result for {msg.case_id} to {original_sender}")
        return

    session = _session(sender)
    _add_timeline(session, f"Specialist result received: {msg.agent_name} ({msg.status})")
    ctx.logger.info(f"{AGENT_NAME}: received specialist result from {sender}: {msg.status}")


@payment_proto.on_message(CommitPayment)
async def handle_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    reference = msg.reference or ""
    pending = None
    for candidate in [reference, msg.transaction_id]:
        if candidate:
            pending = _load_pending_by_reference(ctx, candidate)
            if pending is not None:
                break
    if pending is None:
        pending = _load_pending_by_sender(ctx, sender)

    if pending is None:
        await ctx.send(
            sender,
            CancelPayment(
                transaction_id=msg.transaction_id,
                reason="Payment session not found or expired. Please send the CareLoop request again.",
            ),
        )
        return

    session = _session(pending.original_sender)
    if pending.quote.reference in session.completed_payment_references:
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id or pending.quote.reference))
        if pending.route == APPOINTMENT_AGENT_NAME and session.last_appointment_search is not None:
            await ctx.send(pending.original_sender, create_text_chat(format_appointment_search_preview(session.last_appointment_search)))
        elif pending.route == PHARMACY_ASSISTANT_AGENT_NAME and session.last_otc_order is not None:
            await ctx.send(pending.original_sender, create_text_chat(format_otc_order_preview(session.last_otc_order)))
        return

    session.completed_payment_references.add(pending.quote.reference)
    _add_timeline(session, f"FET payment completed: {pending.quote.amount} {pending.quote.currency}")
    result_text = _complete_paid_work(pending, session)
    _remove_pending(ctx, pending)
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id or pending.quote.reference))
    await ctx.send(pending.original_sender, create_text_chat(result_text))
    ctx.logger.info(f"{AGENT_NAME}: completed orchestrated paid work for {pending.quote.reference}")


@payment_proto.on_message(RejectPayment)
async def handle_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    pending = _load_pending_by_sender(ctx, sender)
    if pending is None:
        ctx.logger.warning(f"{AGENT_NAME}: reject from {sender} had no pending request")
        return

    session = _session(pending.original_sender)
    _add_timeline(session, f"FET payment rejected for {pending.route}")
    _remove_pending(ctx, pending)
    reason = msg.reason or "payment rejected"
    await ctx.send(
        sender,
        create_text_chat(
            f"Payment was not completed, so I did not run the live search. Reason: {reason}\n\n"
            f"{_format_timeline(session)}"
        ),
    )


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Orchestrator")


agent.include(create_chat_protocol(AGENT_NAME, orchestrator_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
