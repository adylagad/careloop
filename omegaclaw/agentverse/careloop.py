"""OmegaClaw Agentverse skill module for CareLoop.

Copy this file into the official OmegaClaw-Core `agentverse/` package, next to
the Tavily/technical-analysis Agentverse skill modules from the Fetch.ai guide.
It calls the registered CareLoop Agentverse agent through the same `_ask_agent`
helper pattern used by the official OmegaClaw examples.
"""

import asyncio
import os
from uuid import uuid4

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
        return _format_care_result(response)
    except Exception as exc:
        return f"CareLoop Agentverse skill error: {exc}"
