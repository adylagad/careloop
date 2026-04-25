import base64
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image

from models import PrescriptionDocumentRequest


SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".avif",
}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}


MEDICATION_NAMES = {
    "amoxicillin": "Amoxicillin",
    "atorvastatin": "Atorvastatin",
    "lipitor": "Atorvastatin",
    "metformin": "Metformin",
    "lisinopril": "Lisinopril",
    "amlodipine": "Amlodipine",
    "levothyroxine": "Levothyroxine",
    "donepezil": "Donepezil",
    "sertraline": "Sertraline",
    "warfarin": "Warfarin",
    "apixaban": "Apixaban",
    "eliquis": "Apixaban",
    "furosemide": "Furosemide",
    "gabapentin": "Gabapentin",
    "omeprazole": "Omeprazole",
    "pantoprazole": "Pantoprazole",
    "albuterol": "Albuterol",
    "insulin": "Insulin",
}


DIRECTION_HINTS = {
    "qd": "once daily",
    "daily": "once daily",
    "once daily": "once daily",
    "bid": "twice daily",
    "twice daily": "twice daily",
    "tid": "three times daily",
    "three times daily": "three times daily",
    "qid": "four times daily",
    "every morning": "every morning",
    "bedtime": "at bedtime",
    "with food": "with food",
    "before meals": "before meals",
    "as needed": "as needed",
    "prn": "as needed",
}

GREETING_TEXT = {
    "hi",
    "hello",
    "hey",
    "hiya",
    "good morning",
    "good afternoon",
    "good evening",
}


@dataclass
class ExtractedPrescription:
    text: str
    source: str
    warnings: list[str]


@dataclass
class PrescriptionItem:
    medication: str
    dose: str
    directions: str
    quantity: str | None = None
    refills: str | None = None
    prescriber: str | None = None
    raw_line: str | None = None


def _clean_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_text_from_pdf(path: Path) -> tuple[str, list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", ["PDF parsing needs the optional `pypdf` package."]

    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        return "", ["No embedded PDF text was found. If this is a scanned PDF, convert pages to images and run OCR."]
    return _clean_text(text), []


def _extract_text_from_image(path: Path) -> tuple[str, list[str]]:
    try:
        import pytesseract
    except ImportError:
        return "", ["Image OCR needs the optional `pytesseract` package and Tesseract binary."]

    try:
        import pillow_avif  # noqa: F401
    except ImportError:
        pass

    try:
        image = Image.open(path).convert("L")
    except Exception as exc:
        if path.suffix.lower() == ".avif":
            return "", ["AVIF decoding needs the optional `pillow-avif-plugin` package."]
        return "", [f"Could not open image for OCR: {exc}"]

    width, height = image.size
    image = image.resize((width * 2, height * 2))
    image = image.point(lambda pixel: 255 if pixel > 180 else 0)
    text = pytesseract.image_to_string(image)
    if not text.strip():
        return "", ["OCR ran but found no readable text. Try a sharper, well-lit photo."]
    return _clean_text(text), []


def _path_from_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme == "":
        return Path(uri)
    return None


def _download_to_temp(uri: str, suffix: str) -> Path:
    response = httpx.get(uri, timeout=30)
    response.raise_for_status()
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(response.content)
    handle.close()
    return Path(handle.name)


def _bytes_to_temp(data: bytes, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(data)
    handle.close()
    return Path(handle.name)


def _guess_suffix(content_type: str | None, fallback: str = ".txt") -> str:
    if content_type:
        lowered = content_type.lower()
        if "pdf" in lowered:
            return ".pdf"
        if "png" in lowered:
            return ".png"
        if "jpeg" in lowered or "jpg" in lowered:
            return ".jpg"
        if "webp" in lowered:
            return ".webp"
        if "avif" in lowered:
            return ".avif"
    return fallback


def _looks_like_binary(data: bytes) -> bool:
    if b"\x00" in data[:1024]:
        return True
    sample = data[:4096]
    if not sample:
        return False
    printable = sum(byte in b"\n\r\t" or 32 <= byte <= 126 for byte in sample)
    return printable / len(sample) < 0.75


def extract_prescription_text(request: PrescriptionDocumentRequest) -> ExtractedPrescription:
    warnings: list[str] = []
    if request.document_text and request.document_text.strip():
        return ExtractedPrescription(_clean_text(request.document_text), "provided text", warnings)

    path: Path | None = None
    source = "unknown document"

    if request.document_path:
        path = Path(request.document_path).expanduser()
        source = str(path)
    elif request.document_uri:
        uri_path = _path_from_uri(request.document_uri)
        if uri_path is not None:
            path = uri_path.expanduser()
            source = request.document_uri
        else:
            suffix = _guess_suffix(request.content_type, Path(urlparse(request.document_uri).path).suffix or ".bin")
            path = _download_to_temp(request.document_uri, suffix)
            source = request.document_uri
    elif request.document_base64:
        suffix = _guess_suffix(request.content_type)
        data = base64.b64decode(request.document_base64)
        path = _bytes_to_temp(data, suffix)
        source = f"base64 {request.content_type or 'document'}"

    if path is None:
        return ExtractedPrescription("", source, ["No prescription text, path, URI, or base64 document was provided."])

    if not path.exists():
        return ExtractedPrescription("", source, [f"Document not found: {path}"])

    suffix = path.suffix.lower()
    if suffix in SUPPORTED_PDF_EXTENSIONS:
        text, extract_warnings = _extract_text_from_pdf(path)
    elif suffix in SUPPORTED_IMAGE_EXTENSIONS:
        text, extract_warnings = _extract_text_from_image(path)
    else:
        data = path.read_bytes()
        if _looks_like_binary(data):
            text = ""
            extract_warnings = [f"Unsupported binary document type: {suffix or 'unknown'}"]
        else:
            text = data.decode(errors="ignore")
            extract_warnings = [] if text.strip() else [f"Unsupported or unreadable document type: {suffix or 'unknown'}"]

    warnings.extend(extract_warnings)
    return ExtractedPrescription(text, source, warnings)


def request_from_chat(case_id: str, user_id: str, text: str) -> PrescriptionDocumentRequest:
    document_uri = None
    document_path = None
    text_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        uri_match = re.search(r"uri=([^\s]+)", stripped)
        if uri_match:
            document_uri = uri_match.group(1)
            continue
        if stripped.startswith("file://"):
            document_uri = stripped
            continue
        if stripped.lower().startswith("file:"):
            document_path = stripped.split(":", 1)[1].strip()
            continue
        text_lines.append(line)

    document_text = "\n".join(text_lines).strip() or None
    return PrescriptionDocumentRequest(
        case_id=case_id,
        user_id=user_id,
        document_text=document_text,
        document_path=document_path,
        document_uri=document_uri,
    )


def _first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def is_greeting(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    return normalized in GREETING_TEXT


def is_help_request(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    help_terms = {
        "help",
        "what can you do",
        "how does this work",
        "what do you do",
        "can you help",
        "how can you help",
    }
    return any(term in normalized for term in help_terms)


def looks_like_prescription_text(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if not normalized:
        return False

    has_known_medication = any(name in normalized for name in MEDICATION_NAMES)
    has_dose = bool(re.search(r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml|units|iu)\b", normalized))
    has_label_terms = any(
        term in normalized
        for term in [
            "rx",
            "sig:",
            "prescriber",
            "refill",
            "refills",
            "qty",
            "quantity",
            "tablet",
            "capsule",
            "pharmacy",
            "take one",
            "take 1",
        ]
    )
    has_direction_terms = any(term in normalized for term in DIRECTION_HINTS)

    return (has_known_medication and (has_dose or has_direction_terms)) or (
        has_dose and has_label_terms
    )


def _dose_match(text: str) -> str | None:
    normalized = (
        text.replace("O", "0")
        .replace("o", "0")
        .replace("S", "5")
        .replace("s", "5")
    )
    match = re.search(r"((?:\d+(?:\.\d+)?)|(?:\.\d+))\s*(mg|mcg|g|ml|units|iu)\b", normalized, re.IGNORECASE)
    if not match:
        return None
    amount = match.group(1)
    if amount.startswith("."):
        amount = f"0{amount}"
    return f"{amount} {match.group(2)}"


def _direction_match(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text.lower())
    frequency_map = {
        "twiceadaybeforemeals": "twice a day before meals",
        "3timesaday": "3 times a day",
        "threetimesaday": "3 times a day",
        "twiceaday": "twice a day",
        "twicedaily": "twice daily",
        "oncedaily": "once daily",
        "asneeded": "as needed",
    }
    for key, value in frequency_map.items():
        if key in compact:
            return value

    match = re.search(
        r"(?:sig|directions?)[:\s]+(.+?)(?:\bqty\b|\bquantity\b|\brefills?\b|\bprescriber\b|\bdoctor\b|\bdr\.|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip(" .;")

    match = re.search(
        r"\btake\s+(.+?)(?:\bqty\b|\bquantity\b|\brefills?\b|\bprescriber\b|\bdoctor\b|\bdr\.|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        return f"Take {match.group(1).strip(' .;')}"
    return None


def _known_medication_in_text(text: str) -> str | None:
    lowered = text.lower()
    return next((display for key, display in MEDICATION_NAMES.items() if key in lowered), None)


def _indication_from_line(line: str) -> str | None:
    known_indications = [
        "bacterial infections",
        "type 2 diabetes",
        "hypertension",
        "hyperlipidemia",
        "asthma or copd exacerbation",
        "asthma",
        "copd",
    ]
    lowered = " ".join(line.lower().split())
    for indication in known_indications:
        if indication in lowered:
            return indication
    return None


def _field_after(pattern: str, text: str) -> str | None:
    return _first_match(pattern, text, re.IGNORECASE)


def parse_prescription_items(text: str) -> list[PrescriptionItem]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = " ".join(lines)
    items: list[PrescriptionItem] = []

    for index, line in enumerate(lines):
        medication = _known_medication_in_text(line)
        dose = _dose_match(line)
        if not medication:
            continue

        window_before = " ".join(lines[max(0, index - 3):index])
        window_after = " ".join(lines[index:index + 5])
        directions = _direction_match(line) or _direction_match(window_before) or _direction_match(window_after)
        if not directions:
            directions = "directions not confidently detected"

        quantity = _field_after(r"(?:qty|quantity)[:#\s]*(\d+)", line) or _field_after(
            r"\(qty\s*(\d+)\)", line
        )
        refills = _field_after(r"refills?[:#\s]+(\d+)", window_after)
        if refills is None and "medication details" in joined.lower():
            line_numbers = re.findall(r"\b(\d+)\b", line)
            if line_numbers:
                refills = line_numbers[-1]
        prescriber = None
        for candidate in lines[index + 1:index + 5]:
            if candidate.lower().startswith("dr.") or "doctor" in candidate.lower() or "prescriber" in candidate.lower():
                prescriber = candidate
                break

        item = PrescriptionItem(
            medication=medication,
            dose=dose or "dose not confidently detected",
            directions=directions,
            quantity=quantity,
            refills=refills,
            prescriber=prescriber,
            raw_line=line,
        )
        indication = _indication_from_line(line)
        if indication:
            item.raw_line = f"{line} | indication: {indication}"
        items.append(
            item
        )

    if items:
        return items

    medication = _known_medication_in_text(joined)
    dose = _dose_match(joined)
    if medication:
        return [
            PrescriptionItem(
                medication=medication,
                dose=dose or "dose not confidently detected",
                directions=_direction_match(joined) or "directions not confidently detected",
                quantity=_field_after(r"(?:qty|quantity)[:#\s]*(\d+)", joined),
                refills=_field_after(r"refills?[:#\s]+(\d+)", joined),
                prescriber=_field_after(r"(?:prescriber|doctor|dr\.)[:\s]+([A-Za-z][A-Za-z .'-]{2,50})", joined),
            )
        ]

    return []


def summarize_prescription_text(text: str, source: str, warnings: list[str]) -> str:
    normalized = " ".join(text.split())
    items = parse_prescription_items(text)
    if not items:
        warning_lines = "\n".join(f"- {warning}" for warning in warnings) or "- The image/text did not contain a medication name I could identify."
        return (
            "I couldn’t confidently read a prescription label from this file.\n\n"
            "Please send a closer, sharper photo where the medication name, strength, and directions are visible, "
            "or paste the label text here.\n\n"
            f"Extraction notes:\n{warning_lines}\n\n"
            "Safety note: I should not guess medication instructions from an unclear image. "
            "Please confirm the label with a pharmacist or clinician."
        )

    warning_lines = "\n".join(f"- {warning}" for warning in warnings)
    if not warning_lines:
        warning_lines = "- None from document extraction."

    details: list[str] = []
    caregiver_meds: list[str] = []
    for idx, item in enumerate(items, start=1):
        details.append(f"{idx}. {item.medication} {item.dose}")
        details.append(f"   Directions: {item.directions}")
        if item.quantity:
            details.append(f"   Quantity: {item.quantity}")
        if item.refills:
            details.append(f"   Refills: {item.refills}")
        if item.prescriber:
            details.append(f"   Prescriber: {item.prescriber}")
        if item.raw_line and " | indication: " in item.raw_line:
            details.append(f"   For: {item.raw_line.split(' | indication: ', 1)[1]}")
        caregiver_meds.append(f"{item.medication} {item.dose}")

    med_phrase = ", ".join(caregiver_meds)

    return (
        "Here is what I could read from the prescription:\n"
        f"{chr(10).join(details)}\n\n"
        "In plain English:\n"
        f"- I found {len(items)} medication label{'s' if len(items) != 1 else ''}: {med_phrase}.\n"
        "- Take each medicine only the way the doctor or pharmacist instructed.\n"
        "- Use a pill organizer or written schedule so doses are not accidentally repeated.\n"
        "- If anything on the label is unclear, ask the pharmacist to read it back slowly before taking the medicine.\n\n"
        "Before taking it, ask:\n"
        "- What time of day should this be taken?\n"
        "- Should it be taken with food?\n"
        "- What should we do if a dose is missed?\n"
        "- Does it interact with other medicines or supplements?\n\n"
        f"Caregiver note: please help confirm {med_phrase}, directions, and refill timing with the pharmacy.\n\n"
        f"Extraction notes:\n{warning_lines}\n\n"
        "Safety note: this is prescription-reading support, not medical advice. "
        "Confirm all medication instructions with the prescribing clinician or pharmacist before use."
    )
