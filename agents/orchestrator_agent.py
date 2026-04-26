from dataclasses import dataclass, field
from time import time

from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import (
    APPOINTMENT_AGENT_NAME,
    PHARMACY_ASSISTANT_AGENT_NAME,
    build_adherence_plan,
    explain_prescription,
    make_case_id,
    notify_caregiver,
    orchestrate_care,
    triage_emergency_reason,
    triage_request,
    triage_route,
)
from models import CareRequest, CareResult


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

care_proto = Protocol(name="CareLoopOrchestratorProtocol", version="0.2.0")
ORCHESTRATOR_CONTEXT_BY_SENDER: dict[str, "OrchestratorSession"] = {}
MAX_ORCHESTRATOR_CONTEXTS = 100


@dataclass
class OrchestratorSession:
    case_id: str
    last_route: str = "clarify"
    last_text: str = ""
    timeline: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time)


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


def _local_result(route: str, request: CareRequest) -> CareResult:
    if route == "careloop-prescription-explainer":
        return explain_prescription(request)
    if route == "careloop-caregiver-notifier":
        return notify_caregiver(request)
    if route == "careloop-adherence":
        return build_adherence_plan(request)
    return triage_request(request)


def _orchestrator_answer(sender: str, text: str) -> str:
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


def orchestrator_chat_response(ctx: Context, sender: str, text: str) -> str:
    return _orchestrator_answer(sender, text)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, orchestrate_care(msg))


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    session = _session(sender)
    session.timeline.append(f"Specialist result received: {msg.agent_name} ({msg.status})")
    ctx.logger.info(f"{AGENT_NAME}: received specialist result from {sender}: {msg.status}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Orchestrator")


agent.include(create_chat_protocol(AGENT_NAME, orchestrator_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
