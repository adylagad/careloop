# CareLoop OmegaClaw + Telegram Track 2 Plan

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

## Track 2 Goal

Demonstrate CareLoop as a specialist Agentverse capability that OmegaClaw can use from
Telegram:

```text
Telegram user -> OmegaClaw / Telegram channel -> Agentverse CareLoop specialist -> Telegram result
```

For the strongest demo, use the doctor-office flow because it is end to end:

1. User asks from Telegram for help with cough/fever near USC.
2. CareLoop offers the Agentverse doctor-office specialist.
3. User confirms.
4. CareLoop creates the Google Calendar appointment.
5. Telegram receives the confirmation.
6. User asks CareLoop to email the caretaker.
7. CareLoop sends the Gmail message after confirmation.

## What Is Implemented In This Repo

`agents/telegram_omegaclaw_bridge.py` is a Telegram Bot API bridge that routes Telegram
messages into the existing CareLoop orchestrator state machine.

It supports:

- `/start` and `/help`.
- Stateful Telegram sessions keyed by chat id.
- Doctor-office offer -> user confirmation -> Calendar booking.
- Existing caregiver email draft/send flow.
- FET payment inside Telegram for paid appointment and OTC pharmacy searches,
  via `/pay` (auto-pay from a demo testnet wallet) and `/paid <tx-hash>`
  (verify a manual transfer on the Fetch.ai stable testnet).

Run it with:

```bash
python agents/telegram_omegaclaw_bridge.py
```

## Environment Variables

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_POLL_SECONDS=1.5

# Telegram-native FET payment (Fetch.ai stable testnet)
TELEGRAM_FET_RECIPIENT=fetch1c7l7snugrzcedpqcxvfsxxv05wl7stqkpre0le
FET_TESTNET_MNEMONIC=
FET_USE_TESTNET=true

DOCTOR_OFFICE_AGENT_ADDRESS=agent1qwt8klq4hwf4gyw0xwu0w9gta23040nxetz34vcnp9g0lp7spw432m8gu72

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
GOOGLE_CALENDAR_DOCTOR_ID=primary
GOOGLE_CALENDAR_PATIENT_EMAIL=adyhacks@gmail.com

GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
GMAIL_FROM_EMAIL=
GMAIL_DEFAULT_TO=adyhacks@gmail.com
```

`TELEGRAM_ALLOWED_CHAT_IDS` is optional. If set, use comma-separated chat IDs.

## Telegram Setup Steps

1. Open Telegram and message `@BotFather`.
2. Send `/newbot`.
3. Choose a bot display name, for example `CareLoop OmegaClaw Demo`.
4. Choose a username ending in `bot`, for example `careloop_omegaclaw_demo_bot`.
5. Copy the bot token.
6. Put it in `.env` as `TELEGRAM_BOT_TOKEN`.
7. Start the bridge:

```bash
python agents/telegram_omegaclaw_bridge.py
```

8. Open the bot in Telegram and send `/start`.
9. Optional: send `/whoami` to get your chat id, then set
   `TELEGRAM_ALLOWED_CHAT_IDS` if you want to restrict the demo bot.

## Demo Script

Telegram:

```text
I have a bad cough and fever near USC. Can you book me a doctor tomorrow morning?
```

Expected:

```text
I found an Agentverse doctor who can book this end to end.

CareLoop Doctor Office: Dr. Maya Patel at CareLoop Family Clinic near USC Village.

This can create the appointment and send the Google Calendar invite. Would you like me to proceed?
```

Telegram:

```text
yes please
```

Expected:

- Calendar event is created on the doctor calendar.
- Patient email receives an invite.
- Telegram receives the booked appointment details.

Then:

```text
write an email to my caretaker saying I booked the appointment and have a bad cough
send it
```

Expected:

- CareLoop drafts the caregiver email.
- `send it` sends it through Gmail.

## OmegaClaw Story To Present

For Track 2, describe this as:

- Specialist capability: `CareLoop Doctor Office`, exposed through Agentverse.
- Channel: Telegram.
- Router: OmegaClaw-style user-facing assistant delegates to CareLoop when the user
  needs care coordination.
- Agentverse invocation: CareLoop Orchestrator routes to `careloop-doctor-office`.
- Result: Calendar appointment confirmation and optional caregiver email returned to
  the Telegram user.

If the official OmegaClaw environment gives you a Telegram/IRC bot runtime, configure
that runtime to call the Agentverse agent:

- Agent handle: `@careloop-orchestrator`
- Primary specialist: `@careloop-doctor-office`
- Doctor-office address:
  `agent1qwt8klq4hwf4gyw0xwu0w9gta23040nxetz34vcnp9g0lp7spw432m8gu72`
- Orchestrator address:
  `agent1qgpgqcj5sgdf35atw8fyeytr49g6tnf8s60rgp6hdm5jeen504r22ut73pf`

## FET Payments Inside Telegram

Telegram does not natively render the ASI:One FET Pay/Reject card, so the bridge
drives FET payment directly. When the orchestrator hands off to a paid route
(appointment search or OTC pharmacy search), the bridge appends a Pay card with
the recipient wallet, amount, and memo, then accepts two completion paths:

1. `/pay` — the bridge auto-sends `0.1 FET` on the Fetch.ai stable testnet from
   the demo wallet (`FET_TESTNET_MNEMONIC` seed phrase) using `cosmpy`, then
   resumes the live search.
2. `/paid <tx-hash>` — the user pays from their own wallet (any Fetch.ai stable
   testnet wallet), then sends the resulting transaction hash. The bridge looks
   the transaction up on chain, confirms it landed, and resumes the live search.

The bridge also supports `/payment` to re-show the current Pay card in case the
user scrolls away from it.

The orchestrator state machine is the same code path used for ASI:One: the
bridge calls `telegram_pending_paid_quote()` to derive the route/quote/request
the orchestrator just queued, settles the payment on-chain, and then calls
`telegram_complete_paid_work()` to run the same `build_appointment_search_quote`
or `build_otc_order_quote` work and update the case timeline.

### Configuration

```bash
# Wallet that receives the CareLoop service fee (defaults to the pharmacy seller wallet)
TELEGRAM_FET_RECIPIENT=fetch1c7l7snugrzcedpqcxvfsxxv05wl7stqkpre0le

# 12 or 24 word seed phrase of a funded stable-testnet wallet, only needed for /pay.
# Manual /paid <tx-hash> verification works without this.
FET_TESTNET_MNEMONIC=

FET_USE_TESTNET=true
```

If `FET_TESTNET_MNEMONIC` is missing or invalid, `/pay` returns a clear error
and the user is prompted to send FET manually and reply with `/paid <tx-hash>`.

### Demo flow

```text
User: Find an MRI scan near USC Village
Bot:  CareLoop quotes 0.1 FET CareLoop service fee + Pay card (recipient,
      amount, memo, web wallet link, /pay and /paid instructions).
User: /pay
Bot:  FET payment confirmed. Running the CareLoop live search now... <results>
```

Or:

```text
User: Find allergy medicine near Westwood for delivery
Bot:  Quote + Pay card.
User: <pays from their wallet, copies tx hash>
User: /paid 0xABC123...
Bot:  FET payment confirmed. Running the CareLoop live search now... <results>
```

## Track 2 Submission Checklist

- Record Telegram conversation.
- Show the Agentverse profile for `careloop-orchestrator`.
- Show the Agentverse profile for `careloop-doctor-office`.
- Show the Calendar event created.
- Show the Gmail caregiver email if using the email follow-up.
- Explain that CareLoop built a new Agentverse specialist capability, not just a
  standalone Telegram bot.
- Explain the routing: OmegaClaw/Telegram identifies care-booking intent, delegates to
  the Agentverse CareLoop capability, then returns the result.

## Track 1 Remaining Checklist

Track 1 is mostly in place. Before submission, finish these polish items:

- README must list all final agent names, addresses, and Agentverse URLs.
- Agent readmes should include the Innovation Lab and hackathon badges.
- Record and save an ASI:One shared chat URL for the orchestrator.
- Include a public GitHub repo URL.
- Include the FET payment story for pharmacy and appointment searches.
- Keep the agents running during judging.
- Verify ASI:One can discover:
  - `@careloop-orchestrator`
  - `@careloop-pharmacy-options`
  - `@careloop-appointment-assistant`
  - `@careloop-doctor-office`
  - `@careloop-prescription-explainer`
  - `@careloop-caregiver-notifier`
