import os

from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import (
    explain_prescription,
    explain_prescription_document,
    make_case_id,
    result_to_text,
)
from models import CareRequest, CareResult, PrescriptionDocumentRequest
from prescription_scanner import (
    is_greeting,
    is_help_request,
    looks_like_prescription_text,
    request_from_chat,
)


AGENT_NAME = "careloop-prescription-explainer"
PORT = env_int("PRESCRIPTION_AGENT_PORT", 8012)
PHARMACY_AGENT_ADDRESS = os.getenv("PHARMACY_AGENT_ADDRESS", "")

agent = create_careloop_agent(
    name=AGENT_NAME,
    port=PORT,
    seed_env="PRESCRIPTION_AGENT_SEED",
    default_seed="careloop prescription explainer seed phrase change me",
    description="CareLoop prescription explainer scans prescription text, PDFs, or photos into senior-friendly guidance.",
)

care_proto = Protocol(name="CareLoopPrescriptionProtocol", version="0.1.0")


def intro_response() -> str:
    return (
        "Hi, I’m CareLoop’s prescription helper. Send me a prescription photo, PDF, "
        "or the text from the label, and I’ll explain it in plain language for an older patient.\n\n"
        "Example: `Rx Lisinopril 10 mg. Sig: Take one tablet by mouth once daily.`"
    )


def prescription_chat_response(ctx: Context, sender: str, text: str) -> str:
    document_request = request_from_chat(make_case_id("chat-rx"), sender, text)
    has_file_input = bool(document_request.document_path or document_request.document_uri)

    if not has_file_input and document_request.document_text:
        if is_greeting(document_request.document_text) or is_help_request(document_request.document_text):
            return intro_response()
        if not looks_like_prescription_text(document_request.document_text):
            return (
                "I can help with prescriptions, but I don’t see a medication label yet. "
                "Please upload a prescription photo/PDF or paste the label text, and I’ll explain it clearly."
            )

    if (
        document_request.document_text
        or document_request.document_path
        or document_request.document_uri
    ):
        return explain_prescription_document(document_request).summary

    request = CareRequest(case_id=document_request.case_id, user_id=sender, text=text)
    return explain_prescription(request).summary


@care_proto.on_message(CareRequest)
async def handle_care_request(ctx: Context, sender: str, msg: CareRequest):
    result = explain_prescription(msg)
    await ctx.send(sender, result)

    pharmacy_address = (msg.context or {}).get("pharmacy_agent_address") or PHARMACY_AGENT_ADDRESS
    if pharmacy_address:
        await ctx.send(pharmacy_address, msg)
        ctx.logger.info(f"{AGENT_NAME}: handed off {msg.case_id} to pharmacy agent")


@care_proto.on_message(PrescriptionDocumentRequest)
async def handle_prescription_document(
    ctx: Context,
    sender: str,
    msg: PrescriptionDocumentRequest,
):
    result = explain_prescription_document(msg)
    await ctx.send(sender, result)

    if result.status == "completed":
        pharmacy_address = (msg.patient_context or {}).get("pharmacy_agent_address") or PHARMACY_AGENT_ADDRESS
        if pharmacy_address:
            await ctx.send(
                pharmacy_address,
                CareRequest(
                    case_id=msg.case_id,
                    user_id=msg.user_id,
                    text=result.summary,
                    context={"source": "prescription-explainer"},
                ),
            )
            ctx.logger.info(f"{AGENT_NAME}: handed off scanned prescription {msg.case_id} to pharmacy agent")


@care_proto.on_message(CareResult)
async def handle_care_result(ctx: Context, sender: str, msg: CareResult):
    ctx.logger.info(f"{AGENT_NAME}: received result from {sender}: {msg.status}")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {ctx.agent.address}")


agent.include(create_chat_protocol(AGENT_NAME, prescription_chat_response), publish_manifest=True)
agent.include(care_proto, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
