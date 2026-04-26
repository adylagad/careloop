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

- `careloop-pharmacy-assistant` — OTC pharmacy ordering specialist and first OmegaClaw
  target. It recommends over-the-counter medicine based on the user's query/address,
  charges a FET service fee, creates an order record, and returns checkout handoff.
- `careloop-prescription-explainer` — senior-friendly mocked prescription explanations.
- `careloop-appointment-assistant` — paid real-data appointment search and booking
  handoff specialist. It searches visible booking/provider data, returns links, and
  keeps state for follow-up questions.
- `careloop-doctor-office` — demo doctor-office agent for a controlled end-to-end
  booking. It can create a Google Calendar event for cough/fever/simple primary-care
  visits and invite the patient email.
- `careloop-caregiver-notifier` — OmegaClaw-friendly caregiver update specialist. It
  turns care events or upstream `CareResult` messages into SMS/email-style caregiver
  notifications with urgency labels, clear next steps, and medication-safety language.
- `careloop-triage` — stateful front-door router. It blocks emergency-like requests
  with escalation guidance, then routes non-emergency prescription, OTC pharmacy,
  appointment, caregiver, and reminder requests to the right specialist handle.
- `careloop-adherence` — mocked medication reminder and missed-dose escalation plan.
- `careloop-orchestrator` — ASI:One-facing coordinator. It runs triage, keeps a
  timeline for the demo, handles safe local flows, and starts paid appointment/pharmacy
  searches with a patient-facing FET service-fee prompt.

For local payment testing, run `payment_buyer_agent.py` as a demo buyer after setting
`PHARMACY_ASSISTANT_AGENT_ADDRESS` to the pharmacy assistant address. Set
`PAYMENT_BUYER_MODE=reject` to test rejection handling. The buyer asks the pharmacy
assistant to recommend and prepare an OTC delivery order.

## Pharmacy Assistant Agent

`careloop-pharmacy-assistant` is an OTC-only specialist. Prescription readiness belongs
to the CareLoop orchestrator because it needs patient care context. This agent handles
queries like "find the best allergy medicine near Westwood" or "order Tylenol for
delivery." It compares online prices it can verify from public quote APIs, shows nearby
offline pickup locations from OpenStreetMap, explains where local shelf prices are not
available from free public APIs, ranks OTC options, includes safety notes, requests a
0.1 FET CareLoop service fee through the Payment Protocol, creates an order record
after payment, and returns a provider checkout handoff. Amazon does not expose a normal
public consumer API for fully automatic checkout, shipping, payment, and purchase
confirmation, so the real fulfillment handoff is a safe provider checkout URL while the
agent-owned order/payment behavior is demonstrated through FET.
The fee is requested before live Browser Use price search or checkout preparation, so
paid work happens only after a `CommitPayment`.
The agent also remembers the latest OTC recommendation per ASI:One sender so follow-up
questions such as "which is nearest to USC Village for pickup?" use the previous medicine
context instead of starting over.

For real data, the agent uses Browser Use Cloud when `BROWSER_USE_API_KEY` is set to
read current public OTC prices from consumer sites such as GoodRx, Walmart, CVS,
Walgreens, Target, Amazon, and Cost Plus Drugs. If Browser Use is unavailable, it falls
back to the Cost Plus Drugs public API for live quoted online prices and OpenStreetMap
Nominatim/Overpass for nearby pharmacy locations. Orchestrator-owned prescription
readiness still needs mocking until a pharmacy/EHR integration is available.
Browser Use results are cached by normalized medicine/location search so repeat prompts
do not spend credits or wait for the same browser task again. Configure with
`CARELOOP_BROWSER_CACHE_TTL_SECONDS` and `CARELOOP_BROWSER_CACHE_PATH`.

Example ASI:One prompts:

```text
Find the best allergy medicine near Westwood and order it for delivery.
Order Tylenol for delivery to Santa Monica.
```

For a real Fetch testnet transaction in the demo buyer, set `FET_ONCHAIN_PAYMENT=true`,
`FET_TESTNET_MNEMONIC` to a funded Dorado/stable testnet wallet, and
`PHARMACY_ASSISTANT_FET_WALLET_ADDRESS` to the seller wallet address. When those are set,
the buyer sends `atestfet` and uses the chain transaction hash as
`CommitPayment.transaction_id`; otherwise it uses the local demo transaction id.

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

## Caregiver Notifier Agent

`careloop-caregiver-notifier` is independently usable from ASI:One and accepts
structured `CareRequest` or `CareResult` messages from other agents. It detects SMS vs
email, caregiver role, patient name, urgency, and care-event type, then drafts a
caregiver-ready update.

Example ASI:One prompts:

```text
Write an SMS to my daughter that Dad's allergy medicine checkout is ready.
Write an email to my son that Mom's doctor appointment is booked tomorrow at 10:30 AM.
Urgent caregiver alert: Mom has chest pain and cannot breathe.
```

The orchestrator can also draft a caregiver email and send it through Gmail after
confirmation. If no recipient email is provided, it defaults to `adyhacks@gmail.com`.

```text
Write an email to my daughter saying I have a bad cough and booked an appointment.
send it
```

## Triage Agent

`careloop-triage` is the ASI:One front door for CareLoop. It remembers recent chat
context, blocks emergency-like symptoms, and routes non-emergency requests to the right
specialist.

Example ASI:One prompts:

```text
Find an MRI scan near USC Village.
Find allergy medicine near UCLA.
Is my prescription ready at CVS?
Tell my daughter Dad's appointment is booked.
My dad has chest pain and cannot breathe.
```

## Orchestrator Agent

`careloop-orchestrator` is the demo-facing CareLoop coordinator. It is stateful, starts
with triage, keeps a demo timeline, and starts paid appointment/pharmacy searches with a
patient-facing FET service-fee step.
For a simple cough/fever primary-care booking near USC, it first offers the
Agentverse-based `careloop-doctor-office` end-to-end booking path, then creates the
Google Calendar appointment only after the user confirms.

Example ASI:One prompts:

```text
Find an MRI scan near USC Village.
I have cough and fever. Book me a doctor tomorrow morning.
yes please
Write a text to my daughter that Dad's appointment is booked tomorrow.
timeline
```

The specialist agents still work independently, but the orchestrator can run the same
appointment/pharmacy logic after its own payment card is completed.

## Appointment Assistant Agent

`careloop-appointment-assistant` is independently usable from ASI:One. It charges a
0.1 FET CareLoop service fee, then searches real public appointment/provider data. When
`BROWSER_USE_API_KEY` is set, it uses Browser Use Cloud to inspect visible booking pages
such as Zocdoc, Healthgrades, provider sites, urgent care pages, and Google Business
profiles. If Browser Use is unavailable, it falls back to the official CMS NPPES public
provider registry plus booking handoff links. It shows cost and earliest availability
only when the live source publishes them.
Browser Use appointment searches share the same normalized cache/dedupe layer as the
pharmacy agent.

Example ASI:One prompts:

```text
Find a primary care doctor near USC Village this week with Medicare.
Find a dermatologist near Westwood.
Which option is closest?
```

Direct automatic booking requires a partner booking API such as Zocdoc for Developers.
The agent is structured so that can be added when credentials are available; until then,
it returns a real booking handoff link and does not claim the appointment is confirmed.

## Doctor Office Agent

`careloop-doctor-office` is the controlled demo path for actual booking behavior. It is
not a provider search engine. It represents a demo doctor office, selects a simple slot
for cough/fever/cold/sore-throat/primary-care requests, creates a Google Calendar event
on the configured doctor calendar, and invites `GOOGLE_CALENDAR_PATIENT_EMAIL`
(`adyhacks@gmail.com` by default).

Example ASI:One prompt through the orchestrator:

```text
I have a bad cough and fever. Book me a doctor tomorrow morning.
```

Calendar config:

```bash
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
GOOGLE_CALENDAR_DOCTOR_ID=primary
GOOGLE_CALENDAR_PATIENT_EMAIL=adyhacks@gmail.com
DOCTOR_OFFICE_AGENT_ADDRESS=
```

## OmegaClaw Telegram Demo

For Track 2, `agents/telegram_omegaclaw_bridge.py` provides a Telegram channel bridge
for the CareLoop orchestrator. It lets a Telegram user ask for care coordination, routes
the request into CareLoop, and returns the result back in Telegram. Use the
doctor-office flow for the cleanest end-to-end OmegaClaw demo because it creates a real
Google Calendar appointment after user confirmation.

```bash
python agents/telegram_omegaclaw_bridge.py
```

See `agents/readmes/omegaclaw_telegram.md` for setup steps, demo script, and the Track 2
submission checklist.

## MCP Servers (Claude Code)

Both Agentverse MCP servers are pre-configured:
- `agentverse-full` — `https://mcp.agentverse.ai/sse`
- `agentverse-lite` — `https://mcp-lite.agentverse.ai/mcp`

## Tracks

- **Track 1**: Agentverse Search & Discovery — agents must implement Chat Protocol
- **Track 2**: OmegaClaw Skill Forge — specialist skills via Agentverse

Promo code for ASI:One Pro + Agentverse Premium: `LAHACKSLAHACKSAV`
