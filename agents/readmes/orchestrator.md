# CareLoop Orchestrator

CareLoop Orchestrator is the demo-facing coordinator for the CareLoop agent system.

It runs triage first, keeps a visible care timeline, handles safe local coordination
flows, and routes paid appointment/pharmacy searches to the selected specialist so the
specialist issues the FET payment card without the user retyping the request.

## Routes

- Prescription explanation: `careloop-prescription-explainer`
- OTC pharmacy/order search: `careloop-pharmacy-options`
- Appointment/provider search: `careloop-appointment-assistant`
- Caregiver updates: `careloop-caregiver-notifier`
- Medication reminders: `careloop-adherence`

## Payment Boundary

The pharmacy and appointment agents remain independently usable. In the orchestrated
flow, CareLoop sends the selected specialist the original ASI buyer address and request
text. The specialist then issues the native FET payment card and returns the result after
payment, so the user does not need to repeat the same request in another chat.
