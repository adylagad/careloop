# CareLoop Official OmegaClaw Track 2 Setup

This folder contains the minimal CareLoop skill wiring for the official Fetch.ai
OmegaClaw Track 2 flow.

The judging story should be:

```text
Telegram -> official OmegaClaw -> Agentverse skill -> careloop-orchestrator -> CareLoop specialists -> Telegram
```

Do not present the custom `telegram_omegaclaw_bridge.py` as the official Track 2
runtime. Keep it only as a fallback demo channel.

## What This Adds

- `agentverse/careloop.py`: Python Agentverse skill module for OmegaClaw.
- `skills_patch.metta`: MeTTa snippets to expose the CareLoop skill to OmegaClaw.

## CareLoop Agentverse Target

Use the orchestrator as the skill target:

```text
careloop-orchestrator
agent1qgpgqcj5sgdf35atw8fyeytr49g6tnf8s60rgp6hdm5jeen504r22ut73pf
```

The orchestrator is already registered on Agentverse and implements the standard
Chat Protocol plus CareLoop structured messages.

## Step-by-Step Setup

### 1. Stop the custom Telegram bridge

Use only one Telegram poller per bot token.

```bash
screen -S careloop-telegram-omegaclaw -X quit || true
```

### 2. Keep CareLoop agents running

At minimum, keep these live:

```text
careloop-orchestrator
careloop-doctor-office
careloop-pharmacy-assistant
careloop-prescription-explainer
careloop-caregiver-notifier
```

### 3. Install official OmegaClaw

Use the hackathon Docker image from the Fetch.ai guide:

```bash
docker pull singularitynet/omegaclaw:hackathon2604
curl -fsSL https://raw.githubusercontent.com/asi-alliance/OmegaClaw-Core/refs/tags/hackathon2604/scripts/omegaclaw | bash -s -- singularitynet/omegaclaw:hackathon2604
```

During setup:

1. Accept the disclaimer.
2. Choose `Telegram`.
3. Paste your Telegram bot token.
4. Choose `ASI:One` as the LLM provider.
5. Paste your ASI:One API key.

### 4. Add the CareLoop skill

For the fastest hackathon path, use a local editable OmegaClaw checkout:

```bash
git clone https://github.com/trueagi-io/PeTTa ~/PeTTa
cd ~/PeTTa
mkdir -p repos
git clone https://github.com/asi-alliance/OmegaClaw-Core.git repos/OmegaClaw-Core
cd repos/OmegaClaw-Core
git fetch origin hackathon-2604
git checkout hackathon-2604
```

Copy the CareLoop module:

```bash
mkdir -p agentverse
cp /Users/aditya/repos/hacks/careloop/omegaclaw/agentverse/careloop.py agentverse/careloop.py
```

Patch `src/skills.metta`:

1. Add the `careloop-healthcare` function from `omegaclaw/skills_patch.metta`.
2. Add the CareLoop skill description line inside `getSkills`.

### 5. Run OmegaClaw with Telegram

From the `~/PeTTa` root:

```bash
export ASI1_API_KEY="your_asi_one_key"
export AGENTVERSE_API_KEY="your_agentverse_key"
export TG_BOT_TOKEN="your_telegram_bot_token"
export CARELOOP_ORCHESTRATOR_AGENT_ADDRESS="agent1qgpgqcj5sgdf35atw8fyeytr49g6tnf8s60rgp6hdm5jeen504r22ut73pf"

sh run.sh run.metta provider=ASIOne commchannel=telegram TG_BOT_TOKEN="$TG_BOT_TOKEN"
```

If the startup script uses different option names, follow the local
`repos/OmegaClaw-Core/scripts/omegaclaw` prompts and keep the same env vars.

### 6. Demo Prompts

Ask OmegaClaw:

```text
What skills do you have available?
```

Then:

```text
Use the CareLoop healthcare skill to help me book a doctor near USC. I have a bad cough.
```

Then:

```text
Use CareLoop to write a caregiver update about the appointment.
```

Then:

```text
Use CareLoop to help me understand this prescription.
```

## What To Say To Judges

CareLoop adds a healthcare coordination skill to official OmegaClaw. OmegaClaw runs
the Telegram interaction, uses ASI:One for reasoning and skill selection, delegates
the healthcare request to CareLoop's registered Agentverse orchestrator, and returns
the result back to Telegram. The specialist capability is not a standalone chatbot;
it is a registered Agentverse skill invoked through OmegaClaw.

## Troubleshooting

- If Telegram says conflict/409, another bot poller is running. Stop the custom
  CareLoop bridge or any previous OmegaClaw container.
- If the skill times out, confirm `careloop-orchestrator` is online on Agentverse.
- If OmegaClaw cannot import `agentverse.careloop`, verify the file was copied into
  the same Python package used by the existing Agentverse skill examples.
- If `_ask_agent` import fails, open the existing Tavily/technical-analysis module in
  that OmegaClaw checkout and mirror its import line.
