import base64
import os
from dataclasses import dataclass
from email.message import EmailMessage

import httpx


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
DEFAULT_CAREGIVER_EMAIL = "adyhacks@gmail.com"


@dataclass
class GmailSendResult:
    message_id: str
    thread_id: str | None
    to_email: str
    subject: str


class GmailDeliveryError(RuntimeError):
    pass


def default_caregiver_email() -> str:
    return os.getenv("GMAIL_DEFAULT_TO") or DEFAULT_CAREGIVER_EMAIL


def gmail_missing_env() -> list[str]:
    required = ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"]
    return [name for name in required if not os.getenv(name)]


def gmail_is_configured() -> bool:
    return not gmail_missing_env()


def _gmail_access_token() -> str:
    missing = gmail_missing_env()
    if missing:
        raise GmailDeliveryError(f"Gmail is not configured. Missing: {', '.join(missing)}")

    response = httpx.post(
        os.getenv("GMAIL_TOKEN_URL", GMAIL_TOKEN_URL),
        data={
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise GmailDeliveryError(f"Google token refresh failed: {response.status_code} {response.text[:240]}")
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise GmailDeliveryError("Google token refresh did not return an access token.")
    return str(token)


def _raw_mime_message(*, to_email: str, subject: str, body: str, from_email: str | None) -> str:
    message = EmailMessage()
    if from_email:
        message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")


def send_gmail_message(
    *,
    to_email: str,
    subject: str,
    body: str,
    from_email: str | None = None,
) -> GmailSendResult:
    token = _gmail_access_token()
    response = httpx.post(
        GMAIL_SEND_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "raw": _raw_mime_message(
                to_email=to_email,
                subject=subject,
                body=body,
                from_email=from_email or os.getenv("GMAIL_FROM_EMAIL"),
            )
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise GmailDeliveryError(f"Gmail send failed: {response.status_code} {response.text[:240]}")
    payload = response.json()
    return GmailSendResult(
        message_id=str(payload.get("id") or ""),
        thread_id=payload.get("threadId"),
        to_email=to_email,
        subject=subject,
    )
