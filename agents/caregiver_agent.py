from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import make_case_id, notify_caregiver, notify_caregiver_from_result
from llm import asi_chat_completion
from models import CareRequest, CareResult


AGENT_NAME = "careloop-caregiver-notifier"
PORT = env_int("CAREGIVER_AGENT_PORT", 8014)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="CAREGIVER_AGENT_SEED",
    default_seed="careloop caregiver notifier seed phrase change me",
    description=(
        "CareLoop Caregiver Notifier drafts stateful SMS/email-style family updates "
        "from care events and upstream agent results."
    ),
    readme_path="agents/readmes/caregiver_notifier.md",
)

care_proto = Protocol(name="CareLoopCaregiverProtocol", version="0.1.0")
CAREGIVER_CONTEXT_BY_SENDER: dict[str, dict[str, str]] = {}
MAX_CAREGIVER_CONTEXTS = 100


def _context_from_chat_text(text: str) -> dict[str, str]:
    normalized = " ".join(text.lower().split())
    context: dict[str, str] = {}
    if "email" in normalized:
        context["channel"] = "email"
    elif "sms" in normalized or "text" in normalized:
        context["channel"] = "sms"
    for label in ["daughter", "son", "wife", "husband", "sister", "brother", "mom", "dad"]:
        if label in normalized:
            context["caregiver"] = label
            break
    if "urgent" in normalized or "emergency" in normalized:
        context["urgency"] = "urgent"
    return context


def _remember_context(sender: str, request: CareRequest, result: CareResult) -> None:
    if len(CAREGIVER_CONTEXT_BY_SENDER) >= MAX_CAREGIVER_CONTEXTS:
        oldest_sender = next(iter(CAREGIVER_CONTEXT_BY_SENDER))
        CAREGIVER_CONTEXT_BY_SENDER.pop(oldest_sender, None)
    context = request.context or {}
    CAREGIVER_CONTEXT_BY_SENDER[sender] = {
        "source_text": request.text,
        "last_summary": result.summary,
        "caregiver": str(context.get("caregiver") or "family caregiver"),
        "patient_name": str(context.get("patient_name") or "the patient"),
        "channel": str(context.get("channel") or "sms"),
        "urgency": str(context.get("urgency") or result.status),
    }


def _is_followup(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    followup_terms = [
        "make it",
        "shorter",
        "more urgent",
        "less urgent",
        "change it",
        "send it",
        "instead",
        "as email",
        "as sms",
        "as text",
        "to my",
        "rewrite",
        "add",
        "remove",
    ]
    return any(term in normalized for term in followup_terms)


def _is_greeting_or_help(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    cleaned = normalized
    if cleaned.startswith("@"):
        parts = cleaned.split(maxsplit=1)
        cleaned = parts[1] if len(parts) > 1 else ""
    return cleaned in {"hi", "hello", "hey", "help", "what can you do", "what do you do"}


def _intro_message() -> str:
    return (
        "Hi, I’m CareLoop Caregiver Notifier. I turn care updates into messages you can send to a family caregiver.\n\n"
        "I can write:\n"
        "- short SMS updates\n"
        "- email summaries\n"
        "- urgent caregiver alerts\n"
        "- follow-up rewrites like “make it shorter” or “send it to my son instead”\n\n"
        "Try:\n"
        "`Write an SMS to my daughter that Dad's allergy medicine checkout is ready.`\n"
        "`Write an email to my son that Mom's appointment is booked tomorrow at 10:30 AM.`"
    )


def _merge_context(previous: dict[str, str], text: str) -> dict[str, str]:
    merged = dict(previous)
    updates = _context_from_chat_text(text)
    merged.update(updates)
    normalized = " ".join(text.lower().split())
    if "email" in normalized:
        merged["channel"] = "email"
    if "sms" in normalized or "text" in normalized:
        merged["channel"] = "sms"
    if "urgent" in normalized:
        merged["urgency"] = "urgent"
    return merged


def _answer_followup(sender: str, text: str, previous: dict[str, str]) -> str:
    merged = _merge_context(previous, text)
    source_text = (
        f"Previous caregiver draft:\n{previous.get('last_summary', '')}\n\n"
        f"User follow-up:\n{text}"
    )
    request = CareRequest(
        case_id=make_case_id("chat-caregiver-followup"),
        user_id=sender,
        text=source_text,
        context=merged,
    )
    result = notify_caregiver(request)
    fallback = result.summary
    llm_answer = asi_chat_completion(
        system_prompt=(
            "You are CareLoop Caregiver Notifier. Revise the previous caregiver message using the user's "
            "follow-up. Keep it concise, preserve known patient/caregiver context, avoid medical advice, "
            "and include a clear next step. Do not invent new clinical facts."
        ),
        user_prompt=(
            f"Known context: {merged}\n\n"
            f"Previous draft:\n{previous.get('last_summary', '')}\n\n"
            f"Follow-up request:\n{text}\n\n"
            "Return the revised caregiver-ready message only, plus urgency and next step."
        ),
        session_id=f"careloop-caregiver-{sender}",
        max_tokens=350,
    )
    final_answer = llm_answer or fallback
    _remember_context(sender, request, result)
    CAREGIVER_CONTEXT_BY_SENDER[sender]["last_summary"] = final_answer
    return final_answer


def caregiver_chat_response(ctx: Context, sender: str, text: str) -> str:
    if _is_greeting_or_help(text):
        return _intro_message()

    previous = CAREGIVER_CONTEXT_BY_SENDER.get(sender)
    if previous and _is_followup(text):
        return _answer_followup(sender, text, previous)

    request = CareRequest(
        case_id=make_case_id("chat-caregiver"),
        user_id=sender,
        text=text,
        context=_context_from_chat_text(text),
    )
    result = notify_caregiver(request)
    fallback = result.summary
    llm_answer = asi_chat_completion(
        system_prompt=(
            "You are CareLoop Caregiver Notifier. Turn care events into concise caregiver-ready "
            "SMS or email messages. Preserve urgency, avoid medical advice, and include a clear next step. "
            "Do not add facts that were not provided."
        ),
        user_prompt=(
            f"User request:\n{text}\n\n"
            f"Draft produced by CareLoop:\n{result.summary}\n\n"
            "Return only the caregiver-ready message plus urgency and next step."
        ),
        session_id=f"careloop-caregiver-{sender}",
        max_tokens=350,
    )
    final_answer = llm_answer or fallback
    _remember_context(sender, request, result)
    CAREGIVER_CONTEXT_BY_SENDER[sender]["last_summary"] = final_answer
    return final_answer


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, notify_caregiver(msg))


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: drafting caregiver update from {msg.agent_name}")
    await ctx.send(sender, notify_caregiver_from_result(msg))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Caregiver Notifier")


agent.include(create_chat_protocol(AGENT_NAME, caregiver_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
