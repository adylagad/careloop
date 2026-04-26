import os
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
from models import CareRequest, CareResult, PaymentQuote


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


@dataclass
class OrchestratorSession:
    case_id: str
    last_route: str = "clarify"
    last_text: str = ""
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


def _is_short_followup(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if normalized.startswith("@"):
        parts = normalized.split(maxsplit=1)
        normalized = parts[1] if len(parts) > 1 else ""
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
        ]
    )


def _intro_message() -> str:
    return (
        "Hi, I’m CareLoop. Tell me what is happening and I’ll coordinate the right specialist.\n\n"
        "I can route prescription questions, OTC medicine orders, appointment searches, caregiver updates, "
        "and reminder planning. Paid live searches happen in the pharmacy or appointment specialist chats so ASI:One can show the FET card."
    )


def _format_timeline(session: OrchestratorSession) -> str:
    if not session.timeline:
        return "CareLoop timeline is empty so far."
    lines = "\n".join(f"{index}. {event}" for index, event in enumerate(session.timeline[-8:], start=1))
    return f"CareLoop timeline\nCase: {session.case_id}\n\n{lines}"


def _specialist_handle(route: str) -> str:
    if route == "careloop-prescription-explainer":
        return "@careloop-prescription-explainer"
    if route == PHARMACY_ASSISTANT_AGENT_NAME:
        return "@careloop-pharmacy-options"
    if route == APPOINTMENT_AGENT_NAME:
        return "@careloop-appointment-assistant"
    if route == "careloop-caregiver-notifier":
        return "@careloop-caregiver-notifier"
    if route == "careloop-adherence":
        return "@careloop-adherence"
    return f"@{route}"


def _paid_handoff(route: str, text: str, session: OrchestratorSession, reason: str) -> str:
    handle = _specialist_handle(route)
    session.timeline.append(f"Paid specialist handoff prepared: {route}")
    return (
        f"CareLoop route: {handle}\n\n"
        f"Why: {reason}\n"
        f"Next: send this to {handle}:\n"
        f"`{text}`\n\n"
        "That specialist will show the FET payment card before running the live search.\n\n"
        f"{_format_timeline(session)}"
    )


def _build_paid_quote(route: str, request: CareRequest) -> PaymentQuote:
    if route == APPOINTMENT_AGENT_NAME:
        return build_appointment_payment_quote(request)
    return build_otc_service_payment_quote(request)


def _format_paid_payment_prompt(route: str, request: CareRequest, quote: PaymentQuote, session: OrchestratorSession) -> str:
    if route == APPOINTMENT_AGENT_NAME:
        return (
            "I can check nearby appointment and imaging options for you.\n\n"
            f"To start the live search, please approve the {quote.amount} FET CareLoop service fee.\n\n"
            "After payment, I’ll show the providers or booking links I can verify. "
            "For MRI scans, many centers require a clinician order or referral before scheduling."
        )

    return (
        "I can compare over-the-counter medicine options and prices for you.\n\n"
        f"To start the live search, please approve the {quote.amount} FET CareLoop service fee.\n\n"
        "After payment, I’ll show the online and pickup options I can verify."
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
    session.timeline.append("Payment requested")
    await _send_payment_request(ctx, sender, route, quote)
    return _format_paid_payment_prompt(route, request, quote, session)


def _local_result(route: str, request: CareRequest) -> CareResult:
    if route == "careloop-prescription-explainer":
        return explain_prescription(request)
    if route == "careloop-caregiver-notifier":
        return notify_caregiver(request)
    if route == "careloop-adherence":
        return build_adherence_plan(request)
    return triage_request(request)


def _complete_paid_work(pending: PendingOrchestratorPayment, session: OrchestratorSession) -> str:
    request = pending.request
    if pending.route == APPOINTMENT_AGENT_NAME:
        search = build_appointment_search_quote(request, pending.quote)
        session.timeline.append("Appointment search completed after FET payment")
        return (
            f"{format_appointment_search_preview(search)}\n\n"
            f"{_format_timeline(session)}"
        )

    order = build_otc_order_quote(request, pending.quote)
    session.timeline.append("OTC pharmacy search completed after FET payment")
    return (
        f"{format_otc_order_preview(order)}\n\n"
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
        session.timeline.append(f"Emergency stop: {emergency_reason}")
        return (
            f"This may be an emergency ({emergency_reason}). Call 911 or local emergency services now.\n\n"
            "CareLoop should not automate this. Notify a caregiver immediately if you can do so without delaying care.\n\n"
            f"{_format_timeline(session)}"
        )

    combined_text = f"{session.last_text}\nFollow-up detail: {text}" if session.last_text and _is_short_followup(text) else text
    decision = triage_route(combined_text)
    route = decision["route"]
    session.last_route = route
    session.last_text = combined_text
    session.timeline.append(f"Triage route: {route} ({decision['confidence']})")

    request = CareRequest(case_id=session.case_id, user_id=sender, text=combined_text)
    if route in {PHARMACY_ASSISTANT_AGENT_NAME, APPOINTMENT_AGENT_NAME}:
        if ctx is None:
            return _paid_handoff(route, combined_text, session, decision["rationale"])
        return await _begin_paid_work(ctx, sender, route, request, session)

    if route == "careloop-orchestrator":
        session.timeline.append("Prescription-readiness flow held for orchestrator context")
        return (
            "CareLoop can coordinate prescription readiness, but the standalone prescription status connector is still mocked.\n\n"
            "For the demo, I’ll keep this as an orchestrator-owned timeline item instead of asking the older adult to know hidden e-prescription details.\n\n"
            f"{_format_timeline(session)}"
        )

    if route == "clarify":
        session.timeline.append("Clarification requested")
        return (
            "I need one detail before coordinating this.\n\n"
            "Is this about a prescription, OTC medicine, an appointment, a caregiver update, or a medication reminder?\n\n"
            f"{_format_timeline(session)}"
        )

    result = _local_result(route, request)
    session.timeline.extend(result.timeline_events or [])
    next_actions = "\n".join(f"- {action}" for action in result.next_actions)
    return (
        f"CareLoop handled this with {_specialist_handle(route)}.\n\n"
        f"{result.summary}\n\n"
        f"Next actions:\n{next_actions}\n\n"
        f"{_format_timeline(session)}"
    )


def _orchestrator_answer_preview(sender: str, text: str) -> str:
    session = _session(sender)
    if _is_greeting_or_help(text):
        return _intro_message()
    if _is_timeline_request(text):
        return _format_timeline(session)

    emergency_reason = triage_emergency_reason(text)
    if emergency_reason:
        session.timeline.append(f"Emergency stop: {emergency_reason}")
        return (
            f"This may be an emergency ({emergency_reason}). Call 911 or local emergency services now.\n\n"
            "CareLoop should not automate this. Notify a caregiver immediately if you can do so without delaying care.\n\n"
            f"{_format_timeline(session)}"
        )

    combined_text = f"{session.last_text}\nFollow-up detail: {text}" if session.last_text and _is_short_followup(text) else text
    decision = triage_route(combined_text)
    route = decision["route"]
    session.last_route = route
    session.last_text = combined_text
    session.timeline.append(f"Triage route: {route} ({decision['confidence']})")

    request = CareRequest(case_id=session.case_id, user_id=sender, text=combined_text)
    if route in {PHARMACY_ASSISTANT_AGENT_NAME, APPOINTMENT_AGENT_NAME}:
        return _paid_handoff(route, combined_text, session, decision["rationale"])

    if route == "careloop-orchestrator":
        session.timeline.append("Prescription-readiness flow held for orchestrator context")
        return (
            "CareLoop can coordinate prescription readiness, but the standalone prescription status connector is still mocked.\n\n"
            "For the demo, I’ll keep this as an orchestrator-owned timeline item instead of asking the older adult to know hidden e-prescription details.\n\n"
            f"{_format_timeline(session)}"
        )

    if route == "clarify":
        session.timeline.append("Clarification requested")
        return (
            "I need one detail before coordinating this.\n\n"
            "Is this about a prescription, OTC medicine, an appointment, a caregiver update, or a medication reminder?\n\n"
            f"{_format_timeline(session)}"
        )

    result = _local_result(route, request)
    session.timeline.extend(result.timeline_events or [])
    next_actions = "\n".join(f"- {action}" for action in result.next_actions)
    return (
        f"CareLoop handled this with {_specialist_handle(route)}.\n\n"
        f"{result.summary}\n\n"
        f"Next actions:\n{next_actions}\n\n"
        f"{_format_timeline(session)}"
    )


def orchestrator_chat_response(ctx: Context, sender: str, text: str) -> str | None:
    if ctx is None:
        return _orchestrator_answer_preview(sender, text)
    return _orchestrator_answer(ctx, sender, text)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, orchestrate_care(msg))


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    session = _session(sender)
    session.timeline.append(f"Specialist result received: {msg.agent_name} ({msg.status})")
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
    session.timeline.append(f"FET payment completed: {pending.quote.amount} {pending.quote.currency}")
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
    session.timeline.append(f"FET payment rejected for {pending.route}")
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
