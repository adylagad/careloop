# CareLoop Triage

CareLoop Triage is the front-door routing specialist for the CareLoop agent system.

It detects emergency-like language and stops automation with clear escalation guidance.
For non-emergency requests, it routes to the right specialist:

- `careloop-prescription-explainer`
- `careloop-pharmacy-options`
- `careloop-appointment-assistant`
- `careloop-caregiver-notifier`
- `careloop-adherence`
- `careloop-orchestrator`

The agent keeps short-term chat context so follow-up details like "near USC" or
"Medicare" continue the previous routing decision instead of starting over.
