from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import make_case_id, result_to_text, triage_request
from models import CareRequest


AGENT_NAME = "careloop-triage"
PORT = env_int("TRIAGE_AGENT_PORT", 8015)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="TRIAGE_AGENT_SEED",
    default_seed="careloop triage seed phrase change me",
    description="CareLoop triage classifies elderly healthcare requests and blocks emergency automation.",
)

care_proto = Protocol(name="CareLoopTriageProtocol", version="0.1.0")


def triage_chat_response(ctx: Context, sender: str, text: str) -> str:
    request = CareRequest(case_id=make_case_id("chat-triage"), user_id=sender, text=text)
    return result_to_text(triage_request(request))


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, triage_request(msg))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")


agent.include(create_chat_protocol(AGENT_NAME, triage_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
