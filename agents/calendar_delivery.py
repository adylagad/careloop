import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import httpx


CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
DEFAULT_PATIENT_EMAIL = "adyhacks@gmail.com"


@dataclass
class CalendarCreateResult:
    event_id: str
    html_link: str | None
    calendar_id: str
    attendee_email: str | None


class CalendarDeliveryError(RuntimeError):
    pass


def calendar_patient_email() -> str:
    return os.getenv("GOOGLE_CALENDAR_PATIENT_EMAIL") or os.getenv("GMAIL_DEFAULT_TO") or DEFAULT_PATIENT_EMAIL


def calendar_doctor_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_DOCTOR_ID") or os.getenv("GOOGLE_CALENDAR_ID") or "primary"


def calendar_missing_env() -> list[str]:
    required = [
        ("GOOGLE_CLIENT_ID", "GMAIL_CLIENT_ID"),
        ("GOOGLE_CLIENT_SECRET", "GMAIL_CLIENT_SECRET"),
        ("GOOGLE_REFRESH_TOKEN", "GMAIL_REFRESH_TOKEN"),
    ]
    missing: list[str] = []
    for preferred, fallback in required:
        if not (os.getenv(preferred) or os.getenv(fallback)):
            missing.append(f"{preferred} or {fallback}")
    return missing


def _env(name: str, fallback_name: str) -> str:
    value = os.getenv(name) or os.getenv(fallback_name)
    if not value:
        raise CalendarDeliveryError(f"Missing env value: {name} or {fallback_name}")
    return value


def _calendar_access_token() -> str:
    missing = calendar_missing_env()
    if missing:
        raise CalendarDeliveryError(f"Google Calendar is not configured. Missing: {', '.join(missing)}")

    response = httpx.post(
        os.getenv("GOOGLE_TOKEN_URL", GOOGLE_TOKEN_URL),
        data={
            "client_id": _env("GOOGLE_CLIENT_ID", "GMAIL_CLIENT_ID"),
            "client_secret": _env("GOOGLE_CLIENT_SECRET", "GMAIL_CLIENT_SECRET"),
            "refresh_token": _env("GOOGLE_REFRESH_TOKEN", "GMAIL_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise CalendarDeliveryError(f"Google token refresh failed: {response.status_code} {response.text[:240]}")
    token = response.json().get("access_token")
    if not token:
        raise CalendarDeliveryError("Google token refresh did not return an access token.")
    return str(token)


def create_calendar_event(
    *,
    calendar_id: str,
    summary: str,
    description: str,
    location: str,
    start: datetime,
    end: datetime,
    timezone: str,
    attendee_email: str | None = None,
) -> CalendarCreateResult:
    token = _calendar_access_token()
    event: dict = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone},
        "reminders": {"useDefault": True},
    }
    if attendee_email:
        event["attendees"] = [{"email": attendee_email}]

    response = httpx.post(
        CALENDAR_EVENTS_URL.format(calendar_id=quote(calendar_id, safe="")),
        params={"sendUpdates": "all"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=event,
        timeout=20,
    )
    if response.status_code >= 400:
        raise CalendarDeliveryError(f"Calendar event create failed: {response.status_code} {response.text[:240]}")
    payload = response.json()
    return CalendarCreateResult(
        event_id=str(payload.get("id") or ""),
        html_link=payload.get("htmlLink"),
        calendar_id=calendar_id,
        attendee_email=attendee_email,
    )
