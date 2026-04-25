from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import build_adherence_plan, make_case_id, result_to_text
from models import CareRequest


AGENT_NAME = "careloop-adherence"
PORT = env_int("ADHERENCE_AGENT_PORT", 8016)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="ADHERENCE_AGENT_SEED",
    default_seed="careloop adherence seed phrase change me",
    description="CareLoop adherence agent creates mocked reminder plans and caregiver escalation rules.",
)

care_proto = Protocol(name="CareLoopAdherenceProtocol", version="0.1.0")


def adherence_chat_response(ctx: Context, sender: str, text: str) -> str:
    request = CareRequest(case_id=make_case_id("chat-adherence"), user_id=sender, text=text)
    return result_to_text(build_adherence_plan(request))


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, build_adherence_plan(msg))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")


agent.include(create_chat_protocol(AGENT_NAME, adherence_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
