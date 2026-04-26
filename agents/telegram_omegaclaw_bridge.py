"""Telegram bridge for the CareLoop OmegaClaw demo.

This bridge is intentionally small: Telegram is the user-facing channel, while
CareLoop Orchestrator keeps the same routing/state behavior used in ASI:One.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv

from orchestrator_agent import orchestrator_chat_response


load_dotenv()

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_POLL_SECONDS = 1.5
MAX_TELEGRAM_MESSAGE_LENGTH = 3900


@dataclass
class TelegramConfig:
    token: str
    allowed_chat_ids: set[str]
    poll_seconds: float


def load_config() -> TelegramConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN. Create a bot with BotFather and put the token in .env.")

    allowed_raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    allowed_chat_ids = {item.strip() for item in allowed_raw.split(",") if item.strip()}
    poll_seconds = float(os.getenv("TELEGRAM_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))
    return TelegramConfig(token=token, allowed_chat_ids=allowed_chat_ids, poll_seconds=poll_seconds)


def telegram_sender_id(chat_id: int | str) -> str:
    return f"telegram:{chat_id}"


def split_telegram_message(text: str) -> list[str]:
    if len(text) <= MAX_TELEGRAM_MESSAGE_LENGTH:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        chunk = remaining[:MAX_TELEGRAM_MESSAGE_LENGTH]
        split_at = max(chunk.rfind("\n\n"), chunk.rfind("\n"), chunk.rfind(" "))
        if split_at < 1200:
            split_at = len(chunk)
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def telegram_api_url(token: str, method: str) -> str:
    return TELEGRAM_API_BASE.format(token=token, method=method)


def is_allowed_chat(config: TelegramConfig, chat_id: int | str) -> bool:
    return not config.allowed_chat_ids or str(chat_id) in config.allowed_chat_ids


def send_message(config: TelegramConfig, chat_id: int | str, text: str) -> None:
    for chunk in split_telegram_message(text):
        response = httpx.post(
            telegram_api_url(config.token, "sendMessage"),
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        response.raise_for_status()


def _update_text(update: dict[str, Any]) -> tuple[int | None, str | None]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    return chat_id, text


def handle_text(config: TelegramConfig, chat_id: int | str, text: str) -> str:
    normalized = text.strip()
    if normalized in {"/start", "/help"}:
        return (
            "Hi, I’m CareLoop on Telegram. Tell me what you need help with.\n\n"
            "Try: I have a bad cough near USC. Can you book me a doctor tomorrow morning?"
        )

    response = orchestrator_chat_response(None, telegram_sender_id(chat_id), normalized)
    return response or "I’m still coordinating that. Please send one more detail."


def run_bridge() -> None:
    config = load_config()
    offset = int(os.getenv("TELEGRAM_START_OFFSET", "0") or "0")
    print("CareLoop Telegram bridge started.")

    while True:
        try:
            response = httpx.get(
                telegram_api_url(config.token, "getUpdates"),
                params={"timeout": 25, "offset": offset, "allowed_updates": ["message", "edited_message"]},
                timeout=35,
            )
            response.raise_for_status()
            payload = response.json()
            for update in payload.get("result", []):
                offset = int(update["update_id"]) + 1
                chat_id, text = _update_text(update)
                if chat_id is None or not text:
                    continue
                if not is_allowed_chat(config, chat_id):
                    send_message(config, chat_id, "This CareLoop demo bot is restricted to approved Telegram chats.")
                    continue
                answer = handle_text(config, chat_id, text)
                send_message(config, chat_id, answer)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Telegram bridge error: {exc}")
            time.sleep(max(config.poll_seconds, 3.0))

        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    run_bridge()
