import os
from dataclasses import dataclass

from uagents import Context, Protocol

from chat_protocol import create_chat_protocol
from config import create_careloop_agent, env_int
from domain import (
    explain_prescription,
    explain_prescription_document,
    make_case_id,
    result_from_extracted_prescription,
    result_to_text,
)
from llm import asi_chat_completion
from models import CareRequest, CareResult, PrescriptionDocumentRequest
from prescription_scanner import (
    PrescriptionItem,
    extract_prescription_text,
    is_greeting,
    is_help_request,
    looks_like_prescription_text,
    parse_prescription_items,
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


@dataclass
class PrescriptionSession:
    extracted_text: str
    summary: str
    items: list[PrescriptionItem]


PRESCRIPTION_CONTEXT_BY_SENDER: dict[str, PrescriptionSession] = {}
MAX_PRESCRIPTION_CONTEXTS = 100


def intro_response() -> str:
    return (
        "Hi, I’m CareLoop’s prescription helper. Send me a prescription photo, PDF, "
        "or the text from the label, and I’ll explain it in plain language for an older patient.\n\n"
        "Example: `Rx Lisinopril 10 mg. Sig: Take one tablet by mouth once daily.`"
    )


def _remember_prescription(sender: str, session: PrescriptionSession) -> None:
    if len(PRESCRIPTION_CONTEXT_BY_SENDER) >= MAX_PRESCRIPTION_CONTEXTS:
        oldest_sender = next(iter(PRESCRIPTION_CONTEXT_BY_SENDER))
        PRESCRIPTION_CONTEXT_BY_SENDER.pop(oldest_sender, None)
    PRESCRIPTION_CONTEXT_BY_SENDER[sender] = session


def _medication_context(items: list[PrescriptionItem]) -> str:
    lines: list[str] = []
    for item in items:
        details = [item.medication, item.dose, item.directions]
        if item.refills:
            details.append(f"refills: {item.refills}")
        if item.raw_line and " | indication: " in item.raw_line:
            details.append(f"for: {item.raw_line.split(' | indication: ', 1)[1]}")
        lines.append(" - " + "; ".join(part for part in details if part))
    return "\n".join(lines)


def _fallback_followup_answer(question: str, session: PrescriptionSession) -> str:
    normalized = " ".join(question.lower().split())
    meds = _medication_context(session.items)
    safety = (
        "This is prescription-reading support, not medical advice. Confirm timing, order, "
        "food instructions, and interactions with the pharmacist or clinician."
    )

    if any(term in normalized for term in ["order", "sequence", "which first", "when should", "schedule"]):
        scheduled = [item for item in session.items if "as needed" not in item.directions.lower()]
        as_needed = [item for item in session.items if "as needed" in item.directions.lower()]
        lines = [
            "There usually is not a single required order unless the prescriber or pharmacist gave one.",
            "Based only on the label text I read, a simple way to organize them is:",
        ]
        for item in scheduled:
            lines.append(f"- {item.medication} {item.dose}: {item.directions}.")
        for item in as_needed:
            lines.append(f"- {item.medication} {item.dose}: use only as needed, following the inhaler/label instructions.")
        lines.append("Keep doses spaced exactly as the label says; do not move doses closer together to catch up.")
        lines.append(safety)
        return "\n".join(lines)

    if any(term in normalized for term in ["with food", "before food", "after food", "meal", "empty stomach"]):
        lines = ["Food timing from the label/context I read:"]
        for item in session.items:
            direction = item.directions.lower()
            if "before meals" in direction:
                food_note = "before meals"
            elif "with food" in direction:
                food_note = "with food"
            else:
                food_note = "not clearly stated on the label I read"
            lines.append(f"- {item.medication} {item.dose}: {food_note}.")
        lines.append(safety)
        return "\n".join(lines)

    if any(term in normalized for term in ["missed", "forgot", "skip"]):
        return (
            "For a missed dose, I should not invent instructions. The safe next step is to call the pharmacist "
            "or check the medication guide for each medicine. Do not double up unless a clinician/pharmacist says so.\n\n"
            f"What I have on file:\n{meds}\n\n{safety}"
        )

    return (
        "Here’s the prescription context I’m using:\n"
        f"{meds}\n\n"
        "Ask me about timing, food, refills, what each medicine is for, or what to confirm with the pharmacist. "
        f"{safety}"
    )


def _answer_followup(sender: str, question: str, session: PrescriptionSession) -> str:
    system_prompt = (
        "You are CareLoop Prescription Explainer, a concise senior-friendly prescription-reading assistant. "
        "Answer the user's follow-up using only the provided prescription context. Do not diagnose, prescribe, "
        "change doses, or invent missing instructions. If the context is insufficient, say what to ask the "
        "pharmacist or clinician. Keep the answer short and conversational."
    )
    user_prompt = (
        "Prescription context:\n"
        f"{_medication_context(session.items)}\n\n"
        "Extracted document text:\n"
        f"{session.extracted_text[:4000]}\n\n"
        f"User question: {question}"
    )
    llm_answer = asi_chat_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        session_id=f"careloop-prescription-{sender}",
    )
    return llm_answer or _fallback_followup_answer(question, session)


def _scan_and_remember(ctx: Context, sender: str, request: PrescriptionDocumentRequest) -> CareResult:
    extracted = extract_prescription_text(request)
    result = result_from_extracted_prescription(request, extracted)
    if result.status == "completed" and extracted.text:
        items = parse_prescription_items(extracted.text)
        if items:
            _remember_prescription(
                sender,
                PrescriptionSession(
                    extracted_text=extracted.text,
                    summary=result.summary,
                    items=items,
                ),
            )
            if ctx is not None:
                ctx.logger.info(f"{AGENT_NAME}: remembered {len(items)} prescription item(s) for {sender}")
    return result


def prescription_chat_response(ctx: Context, sender: str, text: str) -> str:
    document_request = request_from_chat(make_case_id("chat-rx"), sender, text)
    has_file_input = bool(document_request.document_path or document_request.document_uri)
    if ctx is not None:
        ctx.logger.info(
            f"{AGENT_NAME}: chat request text={bool(document_request.document_text)} "
            f"path={bool(document_request.document_path)} uri={bool(document_request.document_uri)}"
        )

    if not has_file_input and document_request.document_text:
        if is_greeting(document_request.document_text) or is_help_request(document_request.document_text):
            return intro_response()
        existing_session = PRESCRIPTION_CONTEXT_BY_SENDER.get(sender)
        if existing_session and not looks_like_prescription_text(document_request.document_text):
            return _answer_followup(sender, document_request.document_text, existing_session)
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
        return _scan_and_remember(ctx, sender, document_request).summary

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
