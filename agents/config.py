import os

from dotenv import load_dotenv
from uagents import Agent


load_dotenv()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def create_careloop_agent(
    *,
    name: str,
    port: int,
    seed_env: str,
    default_seed: str,
    description: str,
) -> Agent:
    agentverse_key = os.getenv("AGENTVERSE_API_KEY") or None
    mailbox = bool(agentverse_key) and env_bool("CARELOOP_MAILBOX", True)
    endpoint = None if mailbox else [f"http://localhost:{port}/submit"]

    return Agent(
        name=name,
        seed=os.getenv(seed_env, default_seed),
        port=port,
        endpoint=endpoint,
        agentverse=agentverse_key,
        mailbox=mailbox,
        publish_agent_details=bool(agentverse_key),
        metadata={
            "description": description,
            "tags": ["careloop", "la-hacks-2026", "healthcare", "asi-one"],
        },
    )
