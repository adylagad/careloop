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

- `careloop-pharmacy-assistant` — broad pharmacy specialist and first OmegaClaw target.
  It starts with doctor-sent prescription readiness checks, paid FET monitoring, and
  readiness updates. The same agent will own OTC medicine ordering next.
- `careloop-prescription-explainer` — senior-friendly mocked prescription explanations.
- `careloop-appointment-booking` — mocked doctor search, booking, and prep checklist.
- `careloop-caregiver-notifier` — SMS/email-style caregiver updates.
- `careloop-triage` — non-emergency routing and emergency escalation guardrails.
- `careloop-adherence` — mocked medication reminder and missed-dose escalation plan.
- `careloop-orchestrator` — ASI:One-facing care timeline coordinator.

For local payment testing, run `payment_buyer_agent.py` as a demo buyer after setting
`PHARMACY_ASSISTANT_AGENT_ADDRESS` to the pharmacy assistant address. Set
`PAYMENT_BUYER_MODE=reject` to test rejection handling. The buyer asks the pharmacy
assistant to keep checking a doctor-sent prescription until it is ready for pickup.

## Pharmacy Assistant Agent

`careloop-pharmacy-assistant` is framed as the one agent for pharmacy-related work. The
patient does not need to call the pharmacy and does not need to know the medication name
before pickup. The agent uses patient/pharmacy context to look up a mocked pending
prescription, then checks a mocked pharmacy status adapter for states such as received,
in progress, delayed, action needed, ready for pickup, or ready for delivery. One-time
ASI:One chat checks are free previews; paid uAgent requests use the FET Payment Protocol
to unlock active monitoring until a terminal status update is available.

The same agent also handles OTC ordering. It can quote common OTC products, request a
0.05 FET CareLoop service fee through the Payment Protocol, create an order record after
payment, and return a provider checkout handoff. Amazon does not expose a normal public
consumer API for fully automatic checkout, shipping, payment, and purchase confirmation,
so the real fulfillment handoff is a safe provider checkout URL while the agent-owned
order/payment behavior is demonstrated through FET.

For real-data expansion, the next adapter layer can use openFDA/RxNorm/DailyMed for
medication reference data, OpenStreetMap for pharmacy locations, and Cost Plus Drugs or
GoodRx-style APIs for price/formulary data. Live patient-specific readiness still needs
mocking until a pharmacy/EHR integration is available.

Example ASI:One prompts:

```text
Is my prescription ready at CVS Westwood?
Order Tylenol for delivery.
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
