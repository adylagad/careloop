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
    description="CareLoop caregiver notifier drafts concise SMS/email-style family updates.",
)

care_proto = Protocol(name="CareLoopCaregiverProtocol", version="0.1.0")


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


def caregiver_chat_response(ctx: Context, sender: str, text: str) -> str:
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
    return llm_answer or fallback


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
