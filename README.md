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

- `careloop-prescription-status` — Prescription Status, the first OmegaClaw specialist
  target. It assumes the doctor already sent the prescription to a pharmacy and the
  patient may not know the medication name yet. It checks mocked pharmacy readiness,
  starts paid FET monitoring, and sends readiness updates when the status changes.
- `careloop-prescription-explainer` — senior-friendly mocked prescription explanations.
- `careloop-appointment-booking` — mocked doctor search, booking, and prep checklist.
- `careloop-caregiver-notifier` — SMS/email-style caregiver updates.
- `careloop-triage` — non-emergency routing and emergency escalation guardrails.
- `careloop-adherence` — mocked medication reminder and missed-dose escalation plan.
- `careloop-orchestrator` — ASI:One-facing care timeline coordinator.

For local payment testing, run `payment_buyer_agent.py` as a demo buyer after setting
`PRESCRIPTION_STATUS_AGENT_ADDRESS` to the status agent address. Set
`PAYMENT_BUYER_MODE=reject` to test rejection handling. The buyer asks the status agent
to keep checking a doctor-sent prescription until it is ready for pickup.

## Prescription Status Agent

`careloop-prescription-status` is framed around post-visit prescription status. The
patient does not need to call the pharmacy and does not need to know the medication name
before pickup. The agent uses patient/pharmacy context to look up a mocked pending
prescription, then checks a mocked pharmacy status adapter for states such as received,
in progress, delayed, action needed, ready for pickup, or ready for delivery. One-time
ASI:One chat checks are free previews; paid uAgent requests use the FET Payment Protocol
to unlock active monitoring until a terminal status update is available.

Example ASI:One prompt:

```text
Is my prescription ready at CVS Westwood?
```

## Prescription Agent Document Intake

`careloop-prescription-explainer` can explain pasted prescription text immediately. It
also accepts `PrescriptionDocumentRequest` messages with `document_path`, `document_uri`,
or `document_base64`. PDFs use embedded-text extraction through `pypdf`; photos use
Tesseract OCR through `pytesseract` plus the system `tesseract` binary.
After a scan, the chat agent keeps the latest prescription context per ASI sender so
follow-up questions like timing, order, food, refills, or missed-dose safety can be
answered conversationally. If `ASI1_API_KEY` or `ASI_ONE_API_KEY` is set, follow-ups use
ASI:One chat completions with the extracted prescription as context; otherwise the agent
uses a deterministic local safety fallback.

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
