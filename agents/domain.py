from uuid import uuid4

from models import (
    CareRequest,
    CareResult,
    PaymentQuote,
    PharmacyOption,
    PharmacyRecommendation,
    PrescriptionDocumentRequest,
)
from prescription_scanner import extract_prescription_text, summarize_prescription_text


PHARMACY_SERVICE_FEE_FET = "0.05"


def make_case_id(prefix: str = "case") -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def value_from_context(
    request: CareRequest,
    key: str,
    default: str,
) -> str:
    if request.context and request.context.get(key):
        return str(request.context[key])
    return default


def infer_medication(text: str) -> tuple[str, str]:
    normalized = normalize_text(text)
    medication_map = {
        "atorvastatin": "Atorvastatin",
        "lipitor": "Atorvastatin",
        "metformin": "Metformin",
        "lisinopril": "Lisinopril",
        "amlodipine": "Amlodipine",
        "levothyroxine": "Levothyroxine",
    }
    medication = next(
        (display for key, display in medication_map.items() if key in normalized),
        "Atorvastatin",
    )

    dosage = "20 mg"
    for token in normalized.replace(",", " ").split():
        if token.endswith("mg") and token[:-2].replace(".", "", 1).isdigit():
            dosage = token[:-2] + " mg"
        elif token.isdigit():
            dosage = token + " mg"
    return medication, dosage


def build_pharmacy_recommendation(request: CareRequest) -> PharmacyRecommendation:
    medication, dosage = infer_medication(request.text)
    location = value_from_context(request, "location", "Los Angeles, CA")
    preference = value_from_context(request, "preference", "delivery")
    reference = f"careloop-pharmacy-{request.case_id}-{uuid4().hex[:8]}"
    quote = PaymentQuote(
        case_id=request.case_id,
        service_name="CareLoop Pharmacy Navigator",
        amount=PHARMACY_SERVICE_FEE_FET,
        reference=reference,
    )

    options = [
        PharmacyOption(
            name="Westwood Care Pharmacy",
            price_usd="$11.40",
            availability="In stock",
            eta="Delivery today, 6-8 PM",
            fit_score=96,
            senior_note="Best fit for home delivery and pharmacist callback.",
        ),
        PharmacyOption(
            name="UCLA Community Pharmacy",
            price_usd="$13.25",
            availability="In stock",
            eta="Pickup in 2 hours",
            fit_score=89,
            senior_note="Best if caregiver can pick up after appointment.",
        ),
        PharmacyOption(
            name="Santa Monica Rx",
            price_usd="$9.80",
            availability="Limited stock",
            eta="Delivery tomorrow morning",
            fit_score=84,
            senior_note="Lowest listed price but slower delivery.",
        ),
    ]

    if "pickup" in preference.lower():
        options = sorted(options, key=lambda item: ("pickup" not in item.eta.lower(), -item.fit_score))

    return PharmacyRecommendation(
        medication=medication,
        dosage=dosage,
        location=location,
        preference=preference,
        options=options,
        selected_option=options[0],
        payment_quote=quote,
    )


def pharmacy_paid_result(
    request: CareRequest,
    recommendation: PharmacyRecommendation,
) -> CareResult:
    option = recommendation.selected_option
    summary = (
        f"Recommended {option.name} for {recommendation.medication} "
        f"{recommendation.dosage}: {option.price_usd}, {option.availability}, {option.eta}. "
        f"{option.senior_note}"
    )
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-pharmacy-options",
        status="completed",
        summary=summary,
        next_actions=[
            "Confirm delivery address and caregiver contact.",
            "Ask pharmacist to review dose timing and interactions.",
            "Notify caregiver after pharmacy confirms fulfillment.",
        ],
        timeline_events=[
            "Pharmacy options compared",
            f"Payment completed: {recommendation.payment_quote.amount} FET",
            f"Selected {option.name}",
        ],
    )


def pharmacy_unpaid_result(request: CareRequest, reason: str) -> CareResult:
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-pharmacy-options",
        status="payment_required",
        summary=(
            "Pharmacy comparison is ready, but final ranked recommendations "
            f"are held until the CareLoop Pharmacy Navigator fee is paid. Reason: {reason}"
        ),
        next_actions=[
            "Approve the 0.05 FET service fee to unlock the ranked pharmacy recommendation.",
            "Reject payment to receive only general pharmacy safety guidance.",
        ],
        timeline_events=["Payment requested for pharmacy navigation"],
    )


def format_pharmacy_preview(recommendation: PharmacyRecommendation) -> str:
    quote = recommendation.payment_quote
    option_lines = "\n".join(
        f"{idx}. {option.name} - {option.price_usd}, {option.availability}, {option.eta}"
        for idx, option in enumerate(recommendation.options, start=1)
    )
    return (
        "CareLoop Pharmacy Navigator\n\n"
        f"Medication: {recommendation.medication} {recommendation.dosage}\n"
        f"Location: {recommendation.location}\n"
        f"Preference: {recommendation.preference}\n\n"
        "Ranked mock options:\n"
        f"{option_lines}\n\n"
        f"Service fee: {quote.amount} {quote.currency} via {quote.payment_method}\n"
        f"Payment reference: {quote.reference}\n\n"
        "Payment status: preview only. Final confirmation is unlocked through the "
        "FET Payment Protocol when another uAgent accepts this service fee.\n\n"
        "Senior safety note: confirm the final prescription and timing with the pharmacist or clinician."
    )


def explain_prescription(request: CareRequest) -> CareResult:
    medication, dosage = infer_medication(request.text)
    summary = (
        f"Mock prescription explanation for {medication} {dosage}: take exactly as prescribed, "
        "use a pill organizer, and ask the pharmacist about timing with meals and other medicines. "
        "This is coordination support, not medical advice; confirm with the prescribing clinician."
    )
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-prescription-explainer",
        status="completed",
        summary=summary,
        next_actions=[
            "Ask the clinician or pharmacist about side effects and missed-dose instructions.",
            "Send the medication details to the pharmacy options agent.",
            "Share the plain-language summary with the caregiver.",
        ],
        timeline_events=["Prescription explained", "Pharmacy handoff prepared"],
    )


def explain_prescription_document(request: PrescriptionDocumentRequest) -> CareResult:
    extracted = extract_prescription_text(request)
    if not extracted.text:
        summary = (
            "I could not read the prescription document yet. Please upload a clearer photo, "
            "paste the prescription label text, or install OCR/PDF extraction dependencies. "
            "Safety note: do not guess medication instructions from an unreadable image."
        )
        return CareResult(
            case_id=request.case_id,
            agent_name="careloop-prescription-explainer",
            status="needs_clearer_document",
            summary=summary,
            next_actions=[
                "Take a well-lit photo with the full label visible.",
                "Paste the prescription text if the upload is not readable.",
                "Ask the pharmacist to confirm the medication, dose, and directions.",
            ],
            timeline_events=["Prescription document received", "Document was not readable"],
        )

    summary = summarize_prescription_text(
        extracted.text,
        extracted.source,
        extracted.warnings,
    )
    if summary.startswith("I couldn’t confidently read"):
        return CareResult(
            case_id=request.case_id,
            agent_name="careloop-prescription-explainer",
            status="needs_clearer_document",
            summary=summary,
            next_actions=[
                "Take a closer photo of the prescription label.",
                "Make sure the medication name, strength, and directions are readable.",
                "Ask the pharmacist to confirm the medication instructions.",
            ],
            timeline_events=["Prescription document received", "Prescription details were not confidently detected"],
        )

    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-prescription-explainer",
        status="completed",
        summary=summary,
        next_actions=[
            "Confirm medication name, dose, and directions with the pharmacist.",
            "Share the caregiver summary with family.",
            "Send medication details to the pharmacy options agent when ready.",
        ],
        timeline_events=["Prescription document scanned", "Prescription explained", "Pharmacy handoff prepared"],
    )


def book_appointment(request: CareRequest) -> CareResult:
    summary = (
        "Mock appointment booked with Westwood Senior Care Clinic for tomorrow at 10:30 AM. "
        "Provider: Dr. Maya Chen, geriatric primary care. Bring medication list, insurance card, "
        "recent symptoms, and caregiver contact."
    )
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-appointment-booking",
        status="completed",
        summary=summary,
        next_actions=[
            "Confirm transportation.",
            "Prepare symptom notes and medication list.",
            "Notify caregiver of appointment time.",
        ],
        timeline_events=["Appointment options searched", "Appointment booked"],
    )


def notify_caregiver(request: CareRequest) -> CareResult:
    caregiver = value_from_context(request, "caregiver", "family caregiver")
    summary = (
        f"Caregiver update for {caregiver}: CareLoop has coordinated the latest step. "
        f"Patient request: {request.text}. Please check in today and confirm any transportation, "
        "pickup, or medication questions."
    )
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-caregiver-notifier",
        status="completed",
        summary=summary,
        next_actions=[
            "Send SMS-style caregiver update.",
            "Ask caregiver to confirm receipt.",
        ],
        timeline_events=["Caregiver notification drafted"],
    )


def triage_request(request: CareRequest) -> CareResult:
    normalized = normalize_text(request.text)
    emergency_terms = {
        "chest pain",
        "stroke",
        "can't breathe",
        "cannot breathe",
        "severe bleeding",
        "unconscious",
        "fainting",
    }
    if any(term in normalized for term in emergency_terms):
        return CareResult(
            case_id=request.case_id,
            agent_name="careloop-triage",
            status="urgent_escalation",
            summary=(
                "This may be an emergency. CareLoop should not automate this request. "
                "Call 911 or local emergency services immediately."
            ),
            next_actions=["Call emergency services now.", "Notify caregiver immediately."],
            timeline_events=["Emergency language detected", "Automation stopped"],
        )

    if "prescription" in normalized or "medication" in normalized or "pharmacy" in normalized:
        route = "careloop-prescription-explainer"
    elif "appointment" in normalized or "doctor" in normalized or "clinic" in normalized:
        route = "careloop-appointment-booking"
    else:
        route = "careloop-appointment-booking"

    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-triage",
        status="completed",
        summary=f"Request is non-emergency and should route to {route}.",
        next_actions=[f"Route case to {route}.", "Keep caregiver in the loop."],
        timeline_events=["Triage completed", f"Route selected: {route}"],
    )


def build_adherence_plan(request: CareRequest) -> CareResult:
    medication, dosage = infer_medication(request.text)
    summary = (
        f"Mock adherence plan for {medication} {dosage}: morning reminder at 8:00 AM, "
        "caregiver check-in if two reminders are missed, and weekly refill review."
    )
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-adherence",
        status="completed",
        summary=summary,
        next_actions=[
            "Create daily reminder.",
            "Mark status as planned, reminded, taken, missed, or caregiver_notified.",
            "Escalate to caregiver after repeated misses.",
        ],
        timeline_events=["Adherence plan created"],
    )


def orchestrate_care(request: CareRequest) -> CareResult:
    steps = [
        triage_request(request),
        book_appointment(request),
        explain_prescription(request),
        notify_caregiver(request),
        build_adherence_plan(request),
    ]
    timeline: list[str] = []
    for step in steps:
        timeline.extend(step.timeline_events or [])

    summary = "CareLoop care timeline:\n" + "\n".join(
        f"- {step.agent_name}: {step.summary}" for step in steps
    )
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-orchestrator",
        status="completed",
        summary=summary,
        next_actions=[
            "Invoke careloop-pharmacy-options for paid pharmacy ranking.",
            "Show timeline in the demo flow.",
            "Use ASI:One to ask the orchestrator for the full care journey.",
        ],
        timeline_events=timeline,
    )


def result_to_text(result: CareResult) -> str:
    next_actions = "\n".join(f"- {action}" for action in result.next_actions)
    timeline = "\n".join(f"- {event}" for event in (result.timeline_events or []))
    return (
        f"{result.agent_name}\n"
        f"Status: {result.status}\n\n"
        f"{result.summary}\n\n"
        f"Next actions:\n{next_actions}\n\n"
        f"Timeline:\n{timeline}"
    )
