# CareLoop Caregiver Notifier

CareLoop Caregiver Notifier is a specialist uAgent for turning care events into caregiver-ready updates.

It can be used directly from ASI:One or delegated to by another CareLoop agent. The agent drafts concise SMS or email messages for family caregivers, labels urgency, preserves known patient/caregiver context across follow-up messages, and keeps medication/health wording safely framed as coordination support rather than medical advice.

## What It Handles

- SMS-style family updates.
- Email-style caregiver summaries.
- Urgent caregiver alerts.
- Follow-up edits like "make it shorter", "send it to my son instead", or "make it an email".
- Structured `CareRequest` messages.
- Structured upstream `CareResult` messages from other CareLoop agents.

## Example Prompts

```text
Write an SMS to my daughter that Dad's allergy medicine checkout is ready.
```

```text
Write an email to my son that Mom's doctor appointment is booked tomorrow at 10:30 AM.
```

```text
Urgent caregiver alert: Mom has chest pain and cannot breathe.
```

```text
Make it shorter and send it to my son instead.
```

## Safety

This agent does not diagnose, prescribe, or replace clinician/pharmacist instructions. It drafts caregiver communication and encourages confirmation with the appropriate clinician, pharmacist, or emergency services when needed.

## CareLoop Role

This is an OmegaClaw-friendly specialist capability: "turn care events into caregiver-ready updates." It will later be composed by the CareLoop orchestrator alongside the prescription, pharmacy, appointment, triage, and adherence agents.
