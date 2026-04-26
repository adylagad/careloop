# CareLoop Orchestrator

CareLoop Orchestrator is the demo-facing coordinator for the CareLoop agent system.

It runs triage first, keeps a demo timeline, handles safe local coordination flows, and
starts paid appointment/pharmacy searches with a patient-facing FET service-fee prompt.

## Routes

- Prescription explanation: `careloop-prescription-explainer`
- OTC pharmacy/order search: `careloop-pharmacy-options`
- Appointment/provider search: `careloop-appointment-assistant`
- Caregiver updates: `careloop-caregiver-notifier`
- Medication reminders: `careloop-adherence`

## Payment Boundary

The pharmacy and appointment agents remain independently usable. In the orchestrated
flow, CareLoop keeps the user in the same conversation and uses the standard appointment
or OTC payment metadata so ASI:One can render the native FET payment controls.
