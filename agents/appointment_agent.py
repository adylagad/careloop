from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import book_appointment, make_case_id, result_to_text
from models import CareRequest


AGENT_NAME = "careloop-appointment-booking"
PORT = env_int("APPOINTMENT_AGENT_PORT", 8013)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="APPOINTMENT_AGENT_SEED",
    default_seed="careloop appointment booking seed phrase change me",
    description="CareLoop appointment agent mocks doctor search, booking, and prep checklists.",
)

care_proto = Protocol(name="CareLoopAppointmentProtocol", version="0.1.0")


def appointment_chat_response(ctx: Context, sender: str, text: str) -> str:
    request = CareRequest(case_id=make_case_id("chat-appt"), user_id=sender, text=text)
    return result_to_text(book_appointment(request))


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, book_appointment(msg))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")


agent.include(create_chat_protocol(AGENT_NAME, appointment_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
