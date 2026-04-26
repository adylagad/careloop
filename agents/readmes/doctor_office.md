# CareLoop Doctor Office

CareLoop Doctor Office is a demo bookable clinic agent for cough, fever, cold,
sore throat, and primary-care appointment requests.

## What It Does

- Accepts appointment booking requests over ASI:One Chat Protocol.
- Accepts `CareRequest` messages from the CareLoop Orchestrator.
- Selects a deterministic demo slot.
- Creates a Google Calendar event on the doctor calendar when Calendar OAuth is configured.
- Adds the patient email as an attendee so the patient receives a calendar invite.

## Example Prompts

```text
Book me a doctor for cough and fever tomorrow morning.
Schedule a doctor appointment for sore throat tomorrow afternoon.
```

## Calendar Boundary

This is scheduling support only. It does not diagnose or provide medical advice. For
severe or emergency symptoms, seek urgent or emergency care.
