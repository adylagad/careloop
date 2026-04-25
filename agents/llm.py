import os

import httpx


ASI_CHAT_COMPLETIONS_URL = "https://api.asi1.ai/v1/chat/completions"


def asi_chat_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    session_id: str,
    max_tokens: int = 500,
    temperature: float = 0.2,
) -> str | None:
    api_key = os.getenv("ASI1_API_KEY") or os.getenv("ASI_ONE_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.getenv("ASI1_MODEL", "asi1-mini"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "x-session-id": session_id,
    }

    try:
        response = httpx.post(ASI_CHAT_COMPLETIONS_URL, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    choices = data.get("choices") or []
    if not choices:
        return None

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    return None
