from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import make_case_id, orchestrate_care, result_to_text
from models import CareRequest, CareResult


AGENT_NAME = "careloop-orchestrator"
PORT = env_int("ORCHESTRATOR_AGENT_PORT", 8010)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="ORCHESTRATOR_AGENT_SEED",
    default_seed="careloop orchestrator seed phrase change me",
    description="CareLoop orchestrator coordinates the mocked elderly healthcare journey across specialists.",
)

care_proto = Protocol(name="CareLoopOrchestratorProtocol", version="0.1.0")


def orchestrator_chat_response(ctx: Context, sender: str, text: str) -> str:
    request = CareRequest(case_id=make_case_id("chat-careloop"), user_id=sender, text=text)
    return result_to_text(orchestrate_care(request))


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, orchestrate_care(msg))


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: received specialist result from {sender}: {msg.status}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")


agent.include(create_chat_protocol(AGENT_NAME, orchestrator_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
