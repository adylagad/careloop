# CareLoop Orchestrator

CareLoop Orchestrator is the demo-facing coordinator for the CareLoop agent system.

It runs triage first, keeps a visible care timeline, handles safe local coordination
flows, and can show the FET payment card directly for paid appointment/pharmacy searches.

## Routes

- Prescription explanation: `careloop-prescription-explainer`
- OTC pharmacy/order search: `careloop-pharmacy-options`
- Appointment/provider search: `careloop-appointment-assistant`
- Caregiver updates: `careloop-caregiver-notifier`
- Medication reminders: `careloop-adherence`

## Payment Boundary

The pharmacy and appointment agents remain independently usable. The orchestrator can
also own the FET payment card and run the same specialist search after payment, so the
user does not need to repeat the same request in another chat.
