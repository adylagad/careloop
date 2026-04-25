from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import make_case_id, notify_caregiver, result_to_text
from models import CareRequest


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


def caregiver_chat_response(ctx: Context, sender: str, text: str) -> str:
    request = CareRequest(case_id=make_case_id("chat-caregiver"), user_id=sender, text=text)
    return result_to_text(notify_caregiver(request))


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, notify_caregiver(msg))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")


agent.include(create_chat_protocol(AGENT_NAME, caregiver_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
