# CareLoop + OmegaClaw Technical Overview

This document explains exactly how CareLoop integrates with the official Fetch.ai
OmegaClaw Track 2 flow and how Telegram messages use CareLoop skills.

## One-Line Summary

CareLoop is exposed to OmegaClaw as a MeTTa skill that calls a Python Agentverse
wrapper, which delegates healthcare requests to the registered CareLoop
orchestrator and specialist uAgents.

## End-To-End Flow

```text
Telegram user
  -> official OmegaClaw Telegram runtime
  -> ASI:One reasoning loop
  -> MeTTa skill: careloop-healthcare
  -> Python Agentverse skill wrapper
  -> careloop-orchestrator on Agentverse
  -> CareLoop specialist agents
  -> result returned to Telegram
```

## What Runs Where

| Layer | Component | Role |
| --- | --- | --- |
| User channel | Telegram bot | Receives user messages during the Track 2 demo |
| Runtime | Official OmegaClaw | Runs the Telegram loop, memory, skills, and MeTTa execution |
| LLM | ASI:One | Chooses when to call the CareLoop skill |
| Skill layer | `careloop-healthcare` | OmegaClaw MeTTa skill exposed to the runtime |
| Bridge | `agentverse/careloop.py` | Python wrapper that creates a CareLoop `CareRequest` |
| Agentverse | `careloop-orchestrator` | Registered CareLoop entrypoint agent |
| Specialists | Doctor, pharmacy, prescription, caregiver, triage, reminders | Handle the actual care workflows |

## Files To Show Judges

| File | Purpose |
| --- | --- |
| `omegaclaw/README.md` | Setup instructions for the official OmegaClaw Track 2 flow |
| `omegaclaw/skills_patch.metta` | MeTTa skill binding added to OmegaClaw |
| `omegaclaw/agentverse/careloop.py` | Python Agentverse wrapper called by OmegaClaw |
| `agents/orchestrator_agent.py` | CareLoop orchestrator and routing logic |

## OmegaClaw Skill Registration

OmegaClaw exposes tools through MeTTa skills. CareLoop adds this skill:

```metta
(= (careloop-healthcare $request)
   (py-call (agentverse.careloop careloop_healthcare_request $request)))
```

The skill is advertised inside OmegaClaw's `getSkills` list as:

```text
Coordinate elderly healthcare tasks through CareLoop Agentverse.
Use this first for doctor booking, cough/fever care, prescriptions,
OTC pharmacy, caregiver messages, reminders, or healthcare routing.
```

That means ASI:One can decide to invoke:

```text
careloop-healthcare "Book a doctor near USC for bad cough"
```

instead of only replying with plain text.

## Python Agentverse Wrapper

The skill calls `careloop_healthcare_request()` in:

```text
omegaclaw/agentverse/careloop.py
```

That function creates a structured uAgents model:

```python
class CareRequest(Model):
    case_id: str
    user_id: str
    text: str
    context: dict | None = None
```

It sends the request to the registered CareLoop orchestrator:

```text
careloop-orchestrator
agent1qgpgqcj5sgdf35atw8fyeytr49g6tnf8s60rgp6hdm5jeen504r22ut73pf
```

The request includes context showing it came from OmegaClaw:

```python
context={"source": "official-omegaclaw-agentverse-skill"}
```

## CareLoop Agentverse Agents

CareLoop's orchestrator routes requests to specialized uAgents:

| Agent | Role |
| --- | --- |
| `careloop-orchestrator` | ASI:One-facing entrypoint and router |
| `careloop-doctor-office` | Demo doctor office that can create Calendar appointments |
| `careloop-appointment-assistant` | Live provider/search and booking-link assistant |
| `careloop-pharmacy-assistant` | OTC search, price comparison, and FET-paid checkout handoff |
| `careloop-prescription-explainer` | Prescription photo/PDF explanation |
| `careloop-caregiver-notifier` | Caregiver SMS/email-style updates |
| `careloop-triage` | Intent and urgency routing |
| `careloop-adherence` | Medication reminder planning |

## Why This Is Track 2 Relevant

This is not just a standalone Telegram bot. The Track 2 story is:

1. Telegram is the user-facing channel.
2. Official OmegaClaw runs the agent loop.
3. ASI:One chooses the CareLoop healthcare skill.
4. OmegaClaw executes the MeTTa skill.
5. The skill calls CareLoop on Agentverse.
6. CareLoop delegates to specialist uAgents.
7. The final answer returns to the Telegram user.

## Resilience / Fallback Layer

During testing, the Agentverse sync endpoint sometimes returned a failed delivery
object instead of a normal response envelope. CareLoop handles that defensively.

The wrapper still attempts the Agentverse call first. If the endpoint returns an
empty or failed delivery response, the wrapper returns a safe healthcare-specific
fallback so the elderly user does not see an infrastructure failure.

Judge explanation:

> We use OmegaClaw's official skill path first. The fallback is a resilience
> layer around transient Agentverse sync delivery failures, not the primary
> architecture.

## Telegram Demo Prompts

Use these in order:

```text
What skills do you have available?
```

```text
Use the CareLoop healthcare skill to book a doctor near USC. I have a bad cough.
```

```text
Use CareLoop to write a caregiver update about the appointment.
```

```text
Use CareLoop to help me understand this prescription.
```

## Commands To Prove The Integration

Show live OmegaClaw logs:

```bash
docker logs -f omegaclaw
```

Filter for CareLoop/OmegaClaw skill activity:

```bash
docker logs --tail 300 omegaclaw 2>&1 | grep -i "careloop\|healthcare\|agentverse"
```

Directly test the CareLoop skill inside the OmegaClaw container:

```bash
docker exec omegaclaw sh -lc 'cd /PeTTa/repos/OmegaClaw-Core && PYTHONPATH=src python3 -c "import agentverse; print(agentverse.careloop_healthcare_request(\"Book a doctor near USC for bad cough\", 2))"'
```

Confirm the container is running:

```bash
docker ps --filter name=omegaclaw
```

## Best Judge Answer

> For Track 2, I used the official OmegaClaw Telegram runtime and added CareLoop
> as a MeTTa skill. Telegram messages go into OmegaClaw, ASI:One reasons over
> the available skills, and when the user asks for healthcare help, OmegaClaw
> invokes `careloop-healthcare`. That calls a Python Agentverse wrapper, which
> sends a structured `CareRequest` to my registered `careloop-orchestrator`.
> The orchestrator then routes to specialist uAgents like doctor office,
> pharmacy, prescription explainer, caregiver notifier, triage, and reminders.
