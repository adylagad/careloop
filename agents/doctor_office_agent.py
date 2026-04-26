from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from doctor_office import AGENT_NAME, book_doctor_office_appointment, is_doctor_office_booking_intent
from domain import make_case_id
from models import CareRequest


PORT = env_int("DOCTOR_OFFICE_AGENT_PORT", 8018)

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="DOCTOR_OFFICE_AGENT_SEED",
    default_seed="careloop doctor office seed phrase change me",
    description=(
        "CareLoop Doctor Office is a demo bookable clinic agent for cough, fever, "
        "and primary-care appointment requests. It can create Google Calendar events."
    ),
    readme_path="agents/readmes/doctor_office.md",
)

care_proto = Protocol(name="CareLoopDoctorOfficeProtocol", version="0.1.0")


def doctor_office_chat_response(ctx: Context, sender: str, text: str) -> str:
    normalized = " ".join(text.lower().split())
    if normalized in {"hi", "hello", "hey", "help", "what can you do", "what do you do"}:
        return (
            "Hi, I’m CareLoop Doctor Office. I can book a demo primary-care slot for cough, fever, cold, "
            "or sore throat requests and create a Google Calendar invite when configured."
        )
    request = CareRequest(case_id=make_case_id("doctor-office"), user_id=sender, text=text)
    if not is_doctor_office_booking_intent(text):
        return "I can book demo doctor appointments for cough, fever, cold, sore throat, or primary-care concerns."
    return book_doctor_office_appointment(request).summary


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    await ctx.send(sender, book_doctor_office_appointment(msg))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")
    ctx.logger.info("OmegaClaw skill target: CareLoop Doctor Office")


agent.include(create_chat_protocol(AGENT_NAME, doctor_office_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
