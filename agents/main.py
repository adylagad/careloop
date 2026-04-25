import os
from uagents import Agent, Bureau
from dotenv import load_dotenv
from chat_protocol import chat_proto

load_dotenv()

SEED = os.getenv("AGENT_SEED", "careloop-agent-seed-phrase-change-me")

agent = Agent(
    name="careloop",
    seed=SEED,
    port=8001,
    endpoint=["http://localhost:8001/submit"],
    agentverse=os.getenv("AGENTVERSE_API_KEY", ""),
)

agent.include(chat_proto, publish_manifest=True)


@agent.on_event("startup")
async def startup(ctx):
    ctx.logger.info(f"Careloop agent address: {ctx.agent.address}")


if __name__ == "__main__":
    agent.run()
