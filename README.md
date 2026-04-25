# Careloop — LA Hacks 2026

Built on [Fetch.ai](https://fetch.ai) Agentverse for LA Hacks 2026.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in AGENTVERSE_API_KEY, ANTHROPIC_API_KEY
```

## Run the agents

```bash
cd agents
python pharmacy_agent.py     # start bottom-up with the paid specialist
python main.py               # run all CareLoop specialists together
```

## CareLoop Agent Roadmap

The repo currently focuses only on Fetch.ai/uAgents work. No frontend or backend service
layer is included yet.

- `careloop-pharmacy-options` — Pharmacy Navigator, the first OmegaClaw specialist target,
  with Chat Protocol and FET Payment Protocol seller flow.
- `careloop-prescription-explainer` — senior-friendly mocked prescription explanations.
- `careloop-appointment-booking` — mocked doctor search, booking, and prep checklist.
- `careloop-caregiver-notifier` — SMS/email-style caregiver updates.
- `careloop-triage` — non-emergency routing and emergency escalation guardrails.
- `careloop-adherence` — mocked medication reminder and missed-dose escalation plan.
- `careloop-orchestrator` — ASI:One-facing care timeline coordinator.

For local payment testing, run `payment_buyer_agent.py` as a demo buyer after setting
`PHARMACY_AGENT_ADDRESS` to the pharmacy agent address. Set `PAYMENT_BUYER_MODE=reject`
to test rejection handling.

## Prescription Agent Document Intake

`careloop-prescription-explainer` can explain pasted prescription text immediately. It
also accepts `PrescriptionDocumentRequest` messages with `document_path`, `document_uri`,
or `document_base64`. PDFs use embedded-text extraction through `pypdf`; photos use
Tesseract OCR through `pytesseract` plus the system `tesseract` binary.

ASI:One/resource prompts can include a file/resource URI when supported by the client.
For local testing, a plain chat prompt can reference a local file:

```text
file:/absolute/path/to/prescription.pdf
Please explain this for my elderly mother.
```

## MCP Servers (Claude Code)

Both Agentverse MCP servers are pre-configured:
- `agentverse-full` — `https://mcp.agentverse.ai/sse`
- `agentverse-lite` — `https://mcp-lite.agentverse.ai/mcp`

## Tracks

- **Track 1**: Agentverse Search & Discovery — agents must implement Chat Protocol
- **Track 2**: OmegaClaw Skill Forge — specialist skills via Agentverse

Promo code for ASI:One Pro + Agentverse Premium: `LAHACKSLAHACKSAV`
