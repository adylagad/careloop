from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import (
    APPOINTMENT_AGENT_NAME,
    PHARMACY_ASSISTANT_AGENT_NAME,
    TRIAGE_AGENT_NAME,
    make_case_id,
    result_to_text,
    triage_emergency_reason,
    triage_next_actions,
    triage_request,
    triage_route,
)
from models import CareRequest, CareResult


AGENT_NAME = TRIAGE_AGENT_NAME
PORT = env_int("TRIAGE_AGENT_PORT", 8015)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="TRIAGE_AGENT_SEED",
    default_seed="careloop triage seed phrase change me",
    description=(
        "CareLoop Triage is a stateful ASI:One front-door agent that detects emergency "
        "language and routes non-emergency care requests to the right CareLoop specialist."
    ),
    readme_path="agents/readmes/triage.md",
)

care_proto = Protocol(name="CareLoopTriageProtocol", version="0.2.0")
TRIAGE_CONTEXT_BY_SENDER: dict[str, dict[str, str]] = {}
MAX_TRIAGE_CONTEXTS = 100


def _intro_message() -> str:
    return (
        "Hi, I’m CareLoop Triage. Tell me what you need, and I’ll route you to the right specialist.\n\n"
        "I can route prescription questions, OTC medicine orders, appointment searches, caregiver updates, "
        "and medication reminders. If symptoms sound urgent, I’ll stop automation and tell you to seek emergency care."
    )


def _remember(sender: str, text: str, route: str, confidence: str, rationale: str) -> None:
    if len(TRIAGE_CONTEXT_BY_SENDER) >= MAX_TRIAGE_CONTEXTS:
        oldest_sender = next(iter(TRIAGE_CONTEXT_BY_SENDER))
        TRIAGE_CONTEXT_BY_SENDER.pop(oldest_sender, None)
    TRIAGE_CONTEXT_BY_SENDER[sender] = {
        "last_text": text,
        "route": route,
        "confidence": confidence,
        "rationale": rationale,
    }


def _is_greeting_or_help(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if normalized.startswith("@"):
        parts = normalized.split(maxsplit=1)
        normalized = parts[1] if len(parts) > 1 else ""
    return normalized in {"hi", "hello", "hey", "help", "what can you do", "what do you do"}


def _is_short_followup(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if normalized.startswith("@"):
        parts = normalized.split(maxsplit=1)
        normalized = parts[1] if len(parts) > 1 else ""
    return len(normalized.split()) <= 8 and any(
        term in normalized
        for term in [
            "yes",
            "yeah",
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
        ]
    )


def _format_route(route: str) -> str:
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
    if route == "careloop-orchestrator":
        return "@careloop-orchestrator"
    return route


def _format_triage_answer(text: str, route: str, confidence: str, rationale: str) -> str:
    if route == "clarify":
        return (
            "I need one detail before routing this.\n\n"
            "Is this about a prescription, OTC medicine, an appointment, a caregiver update, or a medication reminder?"
        )

    handle = _format_route(route)
    actions = triage_next_actions(route)
    next_step = actions[0] if actions else f"Route to {handle}."
    payment_note = ""
    if route in {PHARMACY_ASSISTANT_AGENT_NAME, APPOINTMENT_AGENT_NAME}:
        payment_note = "\nPayment: CareLoop will ask for the FET service fee before the live search."

    return (
        f"Route this to {handle}.\n\n"
        f"Why: {rationale}\n"
        f"Confidence: {confidence}\n"
        f"Next step: {next_step}"
        f"{payment_note}"
    )


def triage_chat_response(ctx: Context, sender: str, text: str) -> str:
    if _is_greeting_or_help(text):
        return _intro_message()

    emergency_reason = triage_emergency_reason(text)
    if emergency_reason:
        TRIAGE_CONTEXT_BY_SENDER.pop(sender, None)
        return (
            f"This may be an emergency ({emergency_reason}). Call 911 or local emergency services now.\n\n"
            "CareLoop should not automate this. Notify a caregiver immediately if you can do so without delaying care."
        )

    previous = TRIAGE_CONTEXT_BY_SENDER.get(sender)
    if previous and _is_short_followup(text):
        combined_text = f"{previous.get('last_text', '')}\nFollow-up detail: {text}"
    else:
        combined_text = text

    decision = triage_route(combined_text)
    route = decision["route"]
    confidence = decision["confidence"]
    rationale = decision["rationale"]
    _remember(sender, combined_text, route, confidence, rationale)
    return _format_triage_answer(combined_text, route, confidence, rationale)


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    result = triage_request(msg)
    if result.status != "urgent_escalation":
        decision = triage_route(msg.text)
        _remember(sender, msg.text, decision["route"], decision["confidence"], decision["rationale"])
    await ctx.send(sender, result)


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    request = CareRequest(
        case_id=msg.case_id or make_case_id("triage-result"),
        user_id=sender,
        text=f"Source result from {msg.agent_name}. Status: {msg.status}. Summary: {msg.summary}",
    )
    await ctx.send(sender, triage_request(request))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Triage Router")


agent.include(create_chat_protocol(AGENT_NAME, triage_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
