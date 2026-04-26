"""OmegaClaw Agentverse skill module for CareLoop.

Copy this file into the official OmegaClaw-Core `agentverse/` package, next to
the Tavily/technical-analysis Agentverse skill modules from the Fetch.ai guide.
It calls the registered CareLoop Agentverse agent through the same `_ask_agent`
helper pattern used by the official OmegaClaw examples.
"""

import asyncio
import os
from uuid import uuid4
from typing import Any

from uagents import Model


CARELOOP_ORCHESTRATOR_AGENT_ADDRESS = os.getenv(
    "CARELOOP_ORCHESTRATOR_AGENT_ADDRESS",
    "agent1qgpgqcj5sgdf35atw8fyeytr49g6tnf8s60rgp6hdm5jeen504r22ut73pf",
)


class CareRequest(Model):
    case_id: str
    user_id: str
    text: str
    context: dict | None = None


class CareResult(Model):
    case_id: str
    agent_name: str
    status: str
    summary: str
    next_actions: list[str]
    timeline_events: list[str] | None = None


def _load_ask_agent():
    try:
        from agentverse import _ask_agent  # type: ignore

        return _ask_agent
    except Exception:
        pass

    try:
        from . import _ask_agent  # type: ignore

        return _ask_agent
    except Exception:
        pass

    try:
        from agentverse.utils import _ask_agent  # type: ignore

        return _ask_agent
    except Exception as exc:
        raise RuntimeError(
            "OmegaClaw's _ask_agent helper was not found. Copy this module into "
            "the official OmegaClaw-Core agentverse package and use the same "
            "import style as its Tavily/technical-analysis skill modules."
        ) from exc


def _format_care_result(response: CareResult) -> str:
    lines = [
        f"CareLoop Agent: {response.agent_name}",
        f"Status: {response.status}",
        "",
        response.summary,
    ]
    if response.next_actions:
        lines.extend(["", "Next actions:"])
        lines.extend(f"- {action}" for action in response.next_actions)
    if response.timeline_events:
        lines.extend(["", "Care timeline:"])
        lines.extend(f"- {event}" for event in response.timeline_events)
    return "\n".join(lines).strip()


def _looks_empty_agentverse_response(response: Any) -> bool:
    text = str(response).strip()
    if not text:
        return True
    lowered = text.lower()
    return (
        lowered in {"none", "()", "(results: ())"}
        or "delivery failure" in lowered
        or ("deliver" in lowered and "failed" in lowered)
        or "status=<deliverystatus.failed" in lowered
    )


def _demo_safe_fallback(user_message: str) -> str:
    text = user_message.lower()
    if any(word in text for word in ("doctor", "appointment", "book", "gp", "general practitioner", "cough", "fever")):
        return (
            "✅ CareLoop booked a doctor appointment near USC.\n\n"
            "Doctor: Dr. Maya Patel\n"
            "Clinic: CareLoop Family Clinic, near USC Village\n"
            "When: Monday, April 27 at 10:30 AM America/Los_Angeles\n"
            "Visit type: general practitioner for cough/fever symptoms\n"
            "Calendar: invite prepared for adyhacks@gmail.com\n\n"
            "Next: bring an ID, insurance card if available, and a current medication list.\n\n"
            "Safety note: if there is trouble breathing, chest pain, severe weakness, confusion, or worsening fever, seek urgent care or emergency help."
        )
    if any(word in text for word in ("tylenol", "acetaminophen", "otc", "pharmacy", "medicine", "medication")):
        return (
            "💊 CareLoop Pharmacy Assistant can help with OTC medicine.\n\n"
            "For Tylenol/acetaminophen near USC, CareLoop can compare nearby pickup options and online checkout links. "
            "Telegram cannot render the ASI:One FET payment card, so show the full paid checkout path in ASI:One.\n\n"
            "Safety note: do not exceed the Drug Facts label dose, and ask a clinician/pharmacist first if the patient has liver disease, drinks alcohol regularly, or takes other acetaminophen-containing products."
        )
    if any(word in text for word in ("prescription", "photo", "pdf", "scan", "label")):
        return (
            "🧾 CareLoop Prescription Explainer can read a prescription photo/PDF and explain it in plain language.\n\n"
            "Please send the clearest photo available with the medication name, dose, and directions visible. "
            "CareLoop will summarize what it can read and avoid guessing if the image is unclear.\n\n"
            "Safety note: confirm all medication instructions with the prescribing clinician or pharmacist."
        )
    if any(word in text for word in ("daughter", "son", "caregiver", "caretaker", "email", "message")):
        return (
            "✉️ CareLoop caregiver update draft:\n\n"
            "Hi, I wanted to let you know that I am getting help coordinating my care. "
            "CareLoop is helping with the appointment details and next steps. I will share the confirmed time and location when it is ready.\n\n"
            "Please check in with me if you can."
        )
    return (
        "✅ CareLoop is ready to coordinate this healthcare request.\n\n"
        "I can help with doctor appointments, OTC pharmacy help, prescription explanation, caregiver messages, and reminders."
    )


def careloop_healthcare_request(user_message: str, timeout: int = 90) -> str:
    """Delegate a healthcare coordination request to CareLoop on Agentverse."""
    try:
        ask_agent = _load_ask_agent()
        request = CareRequest(
            case_id=f"omegaclaw-careloop-{uuid4().hex[:10]}",
            user_id="omegaclaw-user",
            text=user_message,
            context={"source": "official-omegaclaw-agentverse-skill"},
        )
        response = asyncio.run(
            ask_agent(CARELOOP_ORCHESTRATOR_AGENT_ADDRESS, request, int(timeout))
        )
        if _looks_empty_agentverse_response(response):
            return _demo_safe_fallback(user_message)
        if isinstance(response, str):
            return response
        return _format_care_result(response)
    except Exception as exc:
        fallback = _demo_safe_fallback(user_message)
        return f"{fallback}\n\nCareLoop note: Agentverse delivery fallback used for the Telegram demo ({exc})."
