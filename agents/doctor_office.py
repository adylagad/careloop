from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

from calendar_delivery import (
    CalendarDeliveryError,
    calendar_doctor_id,
    calendar_missing_env,
    calendar_patient_email,
    create_calendar_event,
)
from models import CareRequest, CareResult


AGENT_NAME = "careloop-doctor-office"
TIMEZONE = "America/Los_Angeles"
DOCTOR_NAME = "Dr. Maya Patel"
CLINIC_NAME = "CareLoop Family Clinic"
CLINIC_LOCATION = "CareLoop Family Clinic, near USC Village, Los Angeles, CA"


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def is_doctor_office_booking_intent(text: str) -> bool:
    normalized = _normalized(text)
    booking_terms = ["book", "schedule", "appointment", "see a doctor", "doctor", "clinic"]
    symptom_terms = ["cough", "fever", "cold", "sore throat", "flu", "primary care", "family doctor"]
    return any(term in normalized for term in booking_terms) and any(term in normalized for term in symptom_terms)


def _target_date(text: str) -> datetime:
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    normalized = _normalized(text)
    if "today" in normalized:
        day = now
    elif "day after tomorrow" in normalized:
        day = now + timedelta(days=2)
    else:
        day = now + timedelta(days=1)
    return day


def _slot_start(text: str) -> datetime:
    day = _target_date(text)
    normalized = _normalized(text)
    hour = 10
    minute = 30
    if "afternoon" in normalized or "2" in normalized:
        hour = 14
        minute = 0
    if "morning" in normalized:
        hour = 10
        minute = 30
    return datetime.combine(day.date(), datetime_time(hour, minute), tzinfo=ZoneInfo(TIMEZONE))


def _patient_email(request: CareRequest) -> str:
    context = request.context or {}
    for key in ["patient_email", "email", "attendee_email"]:
        if context.get(key):
            return str(context[key])
    return calendar_patient_email()


def _summary(start: datetime, calendar_status: str, link: str | None, patient_email: str) -> str:
    when = start.strftime("%A, %B %-d at %-I:%M %p")
    lines = [
        "Appointment booked",
        f"Doctor: {DOCTOR_NAME}",
        f"Clinic: {CLINIC_NAME}",
        f"When: {when} {TIMEZONE}",
        f"Where: {CLINIC_LOCATION}",
        f"Patient invite: {patient_email}",
        f"Calendar: {calendar_status}",
    ]
    if link:
        lines.append(f"Calendar link: {link}")
    lines.append(
        "Safety note: this is scheduling support only. For trouble breathing, chest pain, severe symptoms, or worsening fever, seek urgent care or emergency help."
    )
    return "\n".join(lines)


def book_doctor_office_appointment(request: CareRequest) -> CareResult:
    start = _slot_start(request.text)
    end = start + timedelta(minutes=30)
    patient_email = _patient_email(request)
    description = (
        "CareLoop demo appointment for cough/fever or primary-care symptoms.\n\n"
        f"Original request: {request.text}\n\n"
        "Bring medication list, allergies, symptom timing, temperature readings, and insurance details."
    )

    calendar_status = "mock_confirmed_calendar_not_configured"
    calendar_link = None
    missing = calendar_missing_env()
    if not missing:
        try:
            created = create_calendar_event(
                calendar_id=calendar_doctor_id(),
                summary=f"CareLoop appointment - {DOCTOR_NAME}",
                description=description,
                location=CLINIC_LOCATION,
                start=start,
                end=end,
                timezone=TIMEZONE,
                attendee_email=patient_email,
            )
            calendar_status = f"created ({created.event_id or 'event id pending'})"
            calendar_link = created.html_link
        except CalendarDeliveryError as exc:
            calendar_status = f"calendar_error: {exc}"

    return CareResult(
        case_id=request.case_id,
        agent_name=AGENT_NAME,
        status="booked",
        summary=_summary(start, calendar_status, calendar_link, patient_email),
        next_actions=[
            "Check the Google Calendar invite.",
            "Confirm transportation and insurance details.",
            "Email or text the caregiver with the appointment details.",
        ],
        timeline_events=["Doctor office slot selected", "Doctor appointment booked"],
    )
