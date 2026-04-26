# CareLoop Orchestrator

CareLoop Orchestrator is the demo-facing coordinator for the CareLoop agent system.

It runs triage first, keeps a visible care timeline, handles safe local coordination
flows, and prepares explicit handoffs to paid specialists when ASI:One needs to show a
FET payment card.

## Routes

- Prescription explanation: `careloop-prescription-explainer`
- OTC pharmacy/order search: `careloop-pharmacy-options`
- Appointment/provider search: `careloop-appointment-assistant`
- Caregiver updates: `careloop-caregiver-notifier`
- Medication reminders: `careloop-adherence`

## Payment Boundary

Paid live search remains inside the pharmacy and appointment specialist chats so ASI:One
can render the native Pay/Reject FET card.
