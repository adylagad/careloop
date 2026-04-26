# CareLoop Pharmacy End-to-End Plan

## Goal

Add the same clean demo pattern used by the doctor-office flow:

1. Keep the existing pharmacy/OTC search behavior working as-is.
2. Let the orchestrator offer an Agentverse pharmacy specialist when the query is a good fit.
3. Only proceed after the user confirms.
4. Then run the full pharmacy loop through the pharmacy agent.

This should feel like:

```text
User: I need Tylenol near USC. Can you help?
CareLoop: I found an Agentverse pharmacy assistant that can compare nearby OTC options and prepare checkout. Would you like me to proceed?
User: yes please
CareLoop: Great. I’ll start the pharmacy assistant now.
```

## Current Behavior To Preserve

- `careloop-pharmacy-assistant` remains independently usable from ASI:One.
- OTC comparison still asks for the FET service fee using the chat payment card.
- Existing price comparison, pickup/delivery, nearby pharmacy, Browser Use cache, and checkout handoff behavior should not regress.
- Prescription readiness should remain orchestrator-owned for now, not moved into this OTC pharmacy path.

## New Orchestrator Behavior

The orchestrator should detect OTC medicine intent such as:

- “I need Tylenol near USC.”
- “Where can I get allergy medicine near UCLA?”
- “Find Advil for pickup.”
- “Can you order cough medicine for delivery?”

Instead of immediately starting paid work, it should offer:

```text
I found an Agentverse pharmacy assistant that can compare OTC prices and pickup/delivery options.

CareLoop Pharmacy Assistant can search real online/pickup prices, show nearby options, and prepare checkout after the FET service fee.

Would you like me to proceed?
```

After confirmation, the orchestrator should either:

- route internally to the pharmacy assistant agent, or
- start the orchestrator-owned payment card and run the same pharmacy logic locally.

Recommended for demo consistency: let the orchestrator own the payment card, then run the same deterministic pharmacy completion logic after payment. This avoids ASI:One hiding payment UI inside a second specialist chat.

## Confirmation State

Add pharmacy-specific pending state to `OrchestratorSession`:

```python
pending_pharmacy_request_text: str | None = None
```

Add confirmation detection similar to doctor-office:

```python
yes, yes please, proceed, go ahead, order it, compare prices, do it
```

If the user says “no” or changes topic, clear `pending_pharmacy_request_text`.

## Routing Rules

Use this order:

1. Emergency stop.
2. Send saved caregiver email.
3. Pending doctor confirmation.
4. Pending pharmacy confirmation.
5. Caregiver message/email intent.
6. Saved result follow-up.
7. Current intent classification.

For current intent:

- OTC medicine intent -> pharmacy offer first.
- Prescription status/readiness -> orchestrator-owned prescription readiness, not OTC flow.
- MRI/specialist appointment -> appointment assistant unchanged.
- Simple cough/fever doctor booking -> doctor-office offer unchanged.

## Payment Flow

After the user confirms pharmacy:

1. Build a `CareRequest` from the saved pending pharmacy text.
2. Start the existing orchestrator FET payment request for `careloop-pharmacy-assistant`.
3. Show the ASI:One payment card.
4. On `CommitPayment`, call existing OTC completion logic:
   - `build_otc_order_quote`
   - `format_otc_order_preview`
   - save `session.last_otc_order`
5. Send the result in the same orchestrator chat.

Do not ask for payment before the user confirms the Agentverse pharmacy option.

## Suggested User-Facing Copy

Offer:

```text
I found an Agentverse pharmacy assistant that can handle this.

CareLoop Pharmacy Assistant can compare OTC prices near USC Village, show pickup or delivery options, and prepare checkout.

Would you like me to proceed?
```

After confirmation:

```text
Great. To run the live OTC price comparison and prepare checkout, please approve the 0.1 FET CareLoop service fee.
```

After payment:

```text
Here are the best OTC options I found...
```

## Tests To Add

- OTC request first returns the pharmacy offer, not the payment prompt.
- `yes please` after the offer returns the FET payment prompt/card path.
- MRI request still routes to appointment search/payment.
- Prescription readiness still routes to orchestrator prescription-readiness flow.
- A new OTC request overrides saved appointment context.
- A short follow-up after paid OTC result still answers from `last_otc_order`.
- Repeating the same confirmed OTC request reuses pending payment rather than creating duplicates.

## Demo Script

```text
@careloop-orchestrator I need Tylenol near USC Village. Can you help me get it?
```

Expected:

```text
I found an Agentverse pharmacy assistant that can handle this...
Would you like me to proceed?
```

Then:

```text
yes please
```

Expected:

- ASI:One FET payment card appears.
- After payment, CareLoop returns real/verified OTC price comparison and pickup/delivery options.

## Notes

- Keep this as OTC-only.
- Do not mix hidden e-prescription readiness into this flow.
- If later adding a real pharmacy partner API, the same confirmation state can trigger an actual checkout/order API after FET payment.
