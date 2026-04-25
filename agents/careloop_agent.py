import os
from uagents import Agent, Context, Model
from uagents.setup import fund_agent_if_low
from dotenv import load_dotenv

load_dotenv()


class CareRequest(Model):
    query: str
    user_id: str


class CareResponse(Model):
    response: str
    agent_address: str


SEED = os.getenv("AGENT_SEED", "careloop-agent-seed-phrase-change-me")

agent = Agent(
    name="careloop",
    seed=SEED,
    port=8001,
    endpoint=["http://localhost:8001/submit"],
    agentverse=os.getenv("AGENTVERSE_API_KEY", ""),
)

fund_agent_if_low(agent.wallet.address())


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"Careloop agent started: {ctx.agent.address}")


@agent.on_message(model=CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    ctx.logger.info(f"Received request from {sender}: {msg.query}")

    response = CareResponse(
        response=f"Careloop received: {msg.query}",
        agent_address=ctx.agent.address,
    )
    await ctx.send(sender, response)


if __name__ == "__main__":
    agent.run()
