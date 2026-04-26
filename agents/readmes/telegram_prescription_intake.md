# CareLoop Telegram Prescription Intake Plan

## Goal

Make the Telegram/OmegaClaw demo handle prescription photos and PDFs directly:

```text
Telegram user uploads prescription photo/PDF
CareLoop downloads the file from Telegram
CareLoop scans it with the existing prescription explainer
CareLoop remembers the prescription context for follow-up questions
Telegram user asks: "When do I take these?" or "Is there an order?"
CareLoop answers from the scanned prescription
```

This would be a strong Track 2 demo because it shows an older adult using a familiar
channel, sending a real-world artifact, and getting useful care coordination without
opening ASI:One.

## Why It Does Not Work Today

`agents/telegram_omegaclaw_bridge.py` currently only reads plain text:

```python
text = message.get("text")
```

When a user sends a prescription image in Telegram, Telegram sends it as `message.photo`
or `message.document`, not as `message.text`. If the user adds a caption such as
"help me with this prescription", the bridge sees only the caption or ignores the media
depending on the message shape.

The existing prescription stack already supports documents:

- `PrescriptionDocumentRequest.document_path`
- `PrescriptionDocumentRequest.document_uri`
- `PrescriptionDocumentRequest.document_base64`
- `prescription_chat_response(...)`
- `explain_prescription_document(...)`
- follow-up memory in `PRESCRIPTION_CONTEXT_BY_SENDER`

So the missing piece is Telegram media intake, not the prescription explainer itself.

## Recommended Implementation

Add Telegram media handling to `agents/telegram_omegaclaw_bridge.py`.

1. Update allowed Telegram update types.
   - Keep `message` and `edited_message`.
   - Handle media inside those message payloads.

2. Parse text, caption, photo, and document.
   - `message.text`: normal chat message.
   - `message.caption`: text attached to a photo/PDF.
   - `message.photo`: compressed Telegram photo sizes.
   - `message.document`: original uploaded file, usually better for PDFs and high-quality images.

3. Choose the best prescription file.
   - For `message.photo`, use the largest photo size by `file_size` or dimensions.
   - For `message.document`, accept:
     - `application/pdf`
     - `image/jpeg`
     - `image/png`
     - `image/webp`
     - `image/avif`

4. Download the Telegram file.
   - Call:

```text
GET https://api.telegram.org/bot<token>/getFile?file_id=<file_id>
```

   - Telegram returns a `file_path`.
   - Download bytes from:

```text
https://api.telegram.org/file/bot<token>/<file_path>
```

5. Send the file to the prescription explainer.
   - Preferred: pass `document_base64` into `PrescriptionDocumentRequest`.
   - Include `content_type` from Telegram when available.
   - Use sender id `telegram:<chat_id>` so follow-up memory stays stateful.

6. Route prescription media directly to the prescription explainer.
   - If media is present, do not route through the general orchestrator first.
   - Call the same prescription logic used by ASI:One:

```python
request = PrescriptionDocumentRequest(
    case_id=make_case_id("telegram-rx"),
    user_id=telegram_sender_id(chat_id),
    document_text=caption,
    document_base64=base64.b64encode(file_bytes).decode("utf-8"),
    content_type=content_type,
)
result = explain_prescription_document(request)
```

   - Important: use the prescription agent's scan-and-remember path, or manually store
     the parsed prescription context after scanning, otherwise follow-up questions will
     not be stateful.

7. Keep follow-up questions stateful.
   - After a successful scan, later Telegram text like:

```text
Should I take these in any order?
What is atorvastatin for?
Can you explain the refills?
Write this for my daughter.
```

   should use the previously scanned prescription context.

8. Return elder-friendly output.
   - Keep it short.
   - Mention each medication in a readable list.
   - Do not dump extraction internals unless there is a problem.
   - Include a brief safety note:

```text
Please confirm medication timing and changes with the pharmacist or clinician.
```

## Suggested Code Shape

Add these helpers to `agents/telegram_omegaclaw_bridge.py`:

```python
@dataclass
class TelegramIncoming:
    chat_id: int | None
    text: str | None
    file_id: str | None
    content_type: str | None
    filename: str | None


def _update_incoming(update: dict[str, Any]) -> TelegramIncoming:
    ...


def _best_photo_file_id(message: dict[str, Any]) -> str | None:
    ...


def download_telegram_file(config: TelegramConfig, file_id: str) -> tuple[bytes, str | None]:
    ...


def handle_media(config: TelegramConfig, incoming: TelegramIncoming) -> str:
    ...
```

Then in the polling loop:

```python
incoming = _update_incoming(update)
if incoming.file_id:
    answer = handle_media(config, incoming)
else:
    answer = handle_text(config, incoming.chat_id, incoming.text)
```

## OCR Dependencies

The prescription scanner already supports image OCR through:

- `pytesseract`
- system Tesseract binary
- `Pillow`
- optional `pillow-avif-plugin` for AVIF files

For the best demo, install and verify:

```bash
brew install tesseract
python -m pip install pytesseract pillow pillow-avif-plugin pypdf
```

If the server environment cannot install Tesseract, use one of these fallbacks:

1. Ask users to upload PDFs with embedded text.
2. Add a cloud OCR provider.
3. Use ASI:One/LLM vision if a supported API is available in the hackathon environment.

## Demo Script

Telegram:

```text
Upload prescription image or PDF with caption:
help me with this prescription
```

Expected:

```text
I found 5 medicines on this prescription:

1. Amoxicillin 500 mg - 3 times a day
2. Metformin 500 mg - twice a day
3. Lisinopril 20 mg - once daily
4. Atorvastatin 10 mg - once daily
5. Albuterol inhaler 90 mcg - as needed

Please confirm timing with the pharmacist or clinician.
```

Telegram follow-up:

```text
Is there any order to take the medications?
```

Expected:

```text
The document does not show a required order. It only shows timing:

- Amoxicillin: 3 times a day
- Metformin: twice a day
- Lisinopril: once daily
- Atorvastatin: once daily
- Albuterol inhaler: as needed

Ask the pharmacist if any should be separated from food or other medicines.
```

Telegram caregiver follow-up:

```text
write a message to my daughter explaining this
```

Expected:

```text
Here is a caregiver message:

I uploaded my prescription list. It shows Amoxicillin, Metformin, Lisinopril,
Atorvastatin, and an Albuterol inhaler. Please help me confirm the exact timing
with the pharmacist before I start or change anything.
```

## Acceptance Tests

Add tests in `tests/test_agent_logic.py`:

- Telegram text-only messages still route to the orchestrator.
- Telegram `/whoami` still works.
- Telegram photo message picks the largest `photo` item.
- Telegram document message accepts PDF/image content types.
- Telegram media calls the prescription document path.
- Telegram follow-up after a scan answers using the previous prescription context.
- Unsupported media returns a clear message asking for a photo, PDF, or pasted text.

## Track 2 Story

This can be presented as:

```text
OmegaClaw/Telegram receives a real prescription image.
CareLoop routes the attachment to the Agentverse prescription specialist.
The specialist scans and explains the prescription.
The same Telegram conversation remains stateful for follow-up questions and caregiver updates.
```

This complements the doctor-office demo:

- Doctor-office: end-to-end booking and calendar invite.
- Prescription intake: real-world document understanding and senior-friendly explanation.
