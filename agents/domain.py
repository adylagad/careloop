from uuid import uuid4

from models import (
    AppointmentSearchQuote,
    CareRequest,
    CareResult,
    OTCProduct,
    PaymentQuote,
    PharmacyFulfillmentStatus,
    PharmacyOption,
    PharmacyOrderQuote,
    PharmacyRecommendation,
    PrescriptionDocumentRequest,
)
from appointment_data import (
    appointment_options,
    infer_appointment_specialty,
    infer_appointment_urgency,
    infer_insurance,
    infer_location,
)
from pharmacy_data import (
    browseruse_price_options,
    costplus_price_option,
    enrich_product_with_costplus,
    nearby_pharmacies,
)
from prescription_scanner import (
    ExtractedPrescription,
    extract_prescription_text,
    summarize_prescription_text,
)


PHARMACY_SERVICE_FEE_FET = "0.1"
PHARMACY_MONITOR_CHECK_MINUTES = 15
PHARMACY_ASSISTANT_AGENT_NAME = "careloop-pharmacy-assistant"
OTC_ORDER_SERVICE_FEE_FET = "0.1"
APPOINTMENT_SERVICE_FEE_FET = "0.1"
APPOINTMENT_AGENT_NAME = "careloop-appointment-assistant"
TRIAGE_AGENT_NAME = "careloop-triage"


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


def infer_medication_if_present(text: str) -> tuple[str, str] | None:
    normalized = normalize_text(text)
    medication_map = {
        "atorvastatin": "Atorvastatin",
        "lipitor": "Atorvastatin",
        "metformin": "Metformin",
        "lisinopril": "Lisinopril",
        "amlodipine": "Amlodipine",
        "levothyroxine": "Levothyroxine",
    }
    medication = next((display for key, display in medication_map.items() if key in normalized), None)
    if medication is None:
        return None

    dosage = "dose pending pharmacy confirmation"
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


def _infer_pharmacy_name(text: str) -> str:
    normalized = normalize_text(text)
    pharmacy_map = {
        "cvs": "CVS Pharmacy - Westwood Blvd",
        "walgreens": "Walgreens - Wilshire Blvd",
        "rite aid": "Rite Aid - Santa Monica",
        "ucla": "UCLA Community Pharmacy",
        "westwood": "Westwood Care Pharmacy",
    }
    return next(
        (display for key, display in pharmacy_map.items() if key in normalized),
        "Westwood Care Pharmacy",
    )


def is_otc_order_intent(text: str) -> bool:
    normalized = normalize_text(text)
    explicit_otc_terms = [
        "tylenol",
        "acetaminophen",
        "advil",
        "ibuprofen",
        "claritin",
        "loratadine",
        "tums",
        "antacid",
        "benadryl",
        "diphenhydramine",
        "otc",
        "over the counter",
        "medicine",
        "medication",
        "pharmacy",
    ]
    appointment_terms = [
        "doctor",
        "appointment",
        "provider",
        "physician",
        "clinic",
        "urgent care",
        "mri",
        "scan",
        "imaging",
        "radiology",
    ]
    if any(term in normalized for term in appointment_terms) and not any(
        term in normalized for term in explicit_otc_terms
    ):
        return False

    order_terms = [
        "order",
        "buy",
        "purchase",
        "checkout",
        "add to cart",
        "ship",
        "recommend",
        "find",
        "get",
        "help",
        "need",
        "where do i get",
        "where can i get",
        "where do i buy",
        "where can i buy",
        "best",
        "need something",
        "what should i get",
        "pharmacy",
        "store",
        "near me",
    ]
    otc_terms = [
        "tylenol",
        "acetaminophen",
        "advil",
        "ibuprofen",
        "claritin",
        "loratadine",
        "tums",
        "antacid",
        "benadryl",
        "diphenhydramine",
        "otc",
        "over the counter",
        "pain",
        "fever",
        "headache",
        "allergy",
        "allergies",
        "heartburn",
        "acid",
        "indigestion",
    ]
    return any(term in normalized for term in order_terms) and any(term in normalized for term in otc_terms)


def is_pharmacy_status_intent(text: str) -> bool:
    normalized = normalize_text(text)
    return "prescription" in normalized and any(
        term in normalized
        for term in ["ready", "status", "pickup", "pick up", "pharmacy has", "sent"]
    )


def _mock_otc_catalog() -> list[OTCProduct]:
    return [
        OTCProduct(
            name="Ibuprofen",
            category="pain_or_inflammation",
            active_ingredient="Ibuprofen",
            strength="200 mg",
            package_size="30 tablets",
            unit_price_usd="real price pending",
            availability="Real quote pending",
            provider="Cost Plus Drugs",
            checkout_url="https://www.costplusdrugs.com/medications/ibuprofen-200mg-tablet/",
            fit_score=92,
            reason="Useful for pain with inflammation, if the user can safely take NSAIDs.",
            safety_note="Ask a clinician before use if the patient has stomach bleeding risk, kidney disease, or takes blood thinners.",
        ),
        OTCProduct(
            name="Loratadine",
            category="allergy",
            active_ingredient="Loratadine",
            strength="10 mg",
            package_size="30 tablets",
            unit_price_usd="real price pending",
            availability="Real quote pending",
            provider="Cost Plus Drugs",
            checkout_url="https://www.costplusdrugs.com/medications/Loratadine-10mg-Tablet/",
            fit_score=92,
            reason="Non-drowsy once-daily allergy option for sneezing, runny nose, or itchy eyes.",
            safety_note="Use only as directed on the label. Ask a pharmacist before combining with other allergy medicines.",
        ),
        OTCProduct(
            name="Famotidine",
            category="heartburn",
            active_ingredient="Famotidine",
            strength="20 mg",
            package_size="30 tablets",
            unit_price_usd="real price pending",
            availability="Real quote pending",
            provider="Cost Plus Drugs",
            checkout_url="https://www.costplusdrugs.com/medications/Famotidine-20mg-Tablet/",
            fit_score=90,
            reason="Common OTC option for heartburn or acid indigestion.",
            safety_note="Ask a pharmacist before use if symptoms are severe, frequent, or the patient has kidney disease.",
        ),
        OTCProduct(
            name="Aspirin Low Dose",
            category="pain_or_fever",
            active_ingredient="Aspirin",
            strength="81 mg",
            package_size="30 chewable tablets",
            unit_price_usd="real price pending",
            availability="Real quote pending",
            provider="Cost Plus Drugs",
            checkout_url="https://www.costplusdrugs.com/medications/aspirin-81mg-tablet-chewable-aspirin/",
            fit_score=62,
            reason="Available with real quote data, but not the default pain choice for older adults due bleeding risk.",
            safety_note="Ask a clinician before aspirin use, especially with blood thinners, ulcers, kidney disease, or upcoming procedures.",
        ),
    ]


def _rank_otc_products(text: str) -> list[OTCProduct]:
    normalized = normalize_text(text)
    catalog = _mock_otc_catalog()
    category_scores = {
        "pain_or_fever": 0,
        "pain_or_inflammation": 0,
        "allergy": 0,
        "heartburn": 0,
    }
    if any(term in normalized for term in ["tylenol", "acetaminophen", "fever", "headache"]):
        category_scores["pain_or_fever"] += 30
    if any(term in normalized for term in ["advil", "ibuprofen", "inflammation", "swelling"]):
        category_scores["pain_or_inflammation"] += 30
    if any(term in normalized for term in ["pain", "ache", "aches"]):
        category_scores["pain_or_fever"] += 20
        category_scores["pain_or_inflammation"] += 15
    if any(term in normalized for term in ["claritin", "loratadine", "allergy", "allergies", "sneezing"]):
        category_scores["allergy"] += 35
    if any(term in normalized for term in ["tums", "antacid", "heartburn", "acid", "indigestion"]):
        category_scores["heartburn"] += 35

    def score(product: OTCProduct) -> int:
        return product.fit_score + category_scores.get(product.category, 0)

    return sorted(catalog, key=score, reverse=True)


def _quantity_from_text(text: str) -> int:
    normalized = normalize_text(text).replace(",", " ")
    tokens = normalized.split()
    for index, token in enumerate(tokens):
        if token.isdigit():
            number = int(token)
            if index + 1 < len(tokens) and tokens[index + 1] in {"pack", "packs", "bottle", "bottles"}:
                return max(1, min(number, 3))
    return 1


def build_otc_service_payment_quote(request: CareRequest) -> PaymentQuote:
    return PaymentQuote(
        case_id=request.case_id,
        service_name="CareLoop OTC Recommendation and Order",
        amount=OTC_ORDER_SERVICE_FEE_FET,
        reference=f"careloop-otc-order-{request.case_id}-{uuid4().hex[:8]}",
    )


def format_otc_payment_prompt(request: CareRequest, quote: PaymentQuote) -> str:
    address_hint = value_from_context(request, "address", value_from_context(request, "location", "Los Angeles, CA"))
    user_need = _infer_otc_need(request.text)
    return (
        "CareLoop Pharmacy Assistant\n\n"
        f"I can run a live OTC price comparison for {user_need} near {address_hint}.\n\n"
        "Service fee required before I search live prices and prepare checkout:\n"
        f"- Amount: {quote.amount} {quote.currency}\n"
        f"- Method: {quote.payment_method}\n"
        f"- Reference: {quote.reference}\n\n"
        "After payment, I will compare online and pickup options, show the prices I can verify, "
        "recommend the safest reasonable OTC option, and return the checkout handoff.\n\n"
        "Safety note: this is OTC shopping support, not medical advice. Use the Drug Facts label "
        "and ask a pharmacist if the patient has other conditions or medicines."
    )


def _usd_to_float(value: str) -> float:
    return float(value.replace("$", "").strip())


def build_otc_order_quote(request: CareRequest, payment_quote: PaymentQuote | None = None) -> PharmacyOrderQuote:
    ranked = [enrich_product_with_costplus(product) for product in _rank_otc_products(request.text)]
    product = ranked[0]
    quantity = _quantity_from_text(request.text)
    subtotal = _usd_to_float(product.unit_price_usd) * quantity if product.unit_price_usd.startswith("$") else 0
    address_hint = value_from_context(request, "address", value_from_context(request, "location", "Los Angeles, CA"))
    preference = value_from_context(request, "preference", "delivery")
    locations = nearby_pharmacies(address_hint)
    browser_prices = browseruse_price_options(product, address_hint)
    online_price_options = browser_prices or [
        option
        for option in [costplus_price_option(item) for item in [product, *ranked[1:3]]]
        if option is not None
    ]
    offline_price_options = [
        option for option in online_price_options if any(term in option.fulfillment.lower() for term in ["pickup", "coupon"])
    ]
    quote = payment_quote or build_otc_service_payment_quote(request)

    return PharmacyOrderQuote(
        case_id=request.case_id,
        product=product,
        alternatives=ranked[1:3],
        quantity=quantity,
        subtotal_usd=f"${subtotal:.2f}" if subtotal else "real price unavailable",
        fulfillment_method="Cost Plus checkout handoff",
        address_hint=address_hint,
        user_need=_infer_otc_need(request.text),
        nearby_pharmacies=locations,
        location_source="OpenStreetMap Overpass API" if locations else "location lookup unavailable",
        online_price_options=online_price_options,
        offline_price_options=offline_price_options,
        status="quote_ready",
        payment_quote=quote,
    )


def _infer_otc_need(text: str) -> str:
    normalized = normalize_text(text)
    if any(term in normalized for term in ["allergy", "allergies", "sneezing"]):
        return "allergy relief"
    if any(term in normalized for term in ["heartburn", "acid", "indigestion", "antacid"]):
        return "heartburn or indigestion relief"
    if any(term in normalized for term in ["fever", "headache", "pain", "ache"]):
        return "pain or fever relief"
    return "OTC medicine request"


def format_otc_order_preview(order: PharmacyOrderQuote) -> str:
    online_options = order.online_price_options or []
    online_prices = "\n".join(
        (
            f"- {item.product_name}: {item.price_usd} via {item.merchant}"
            f" ({item.fulfillment}; {item.source})"
        )
        for item in online_options
    )
    if not online_prices:
        online_prices = "- No verified online prices found right now."
    alternatives = "\n".join(
        f"- {item.name}: {item.reason}"
        for item in order.alternatives
    )
    locations = "\n".join(f"- {item}" for item in (order.nearby_pharmacies or []))
    if not locations:
        locations = "- I could not fetch nearby pharmacy locations right now."
    offline_options = order.offline_price_options or []
    offline_prices = "\n".join(
        (
            f"- {item.product_name}: {item.price_usd} via {item.merchant}"
            f" ({item.fulfillment}; {item.source})"
        )
        for item in offline_options
    )
    if not offline_prices:
        offline_prices = "\n".join(
            f"- {item}: live shelf price not found; use this for distance/pickup planning."
            for item in (order.nearby_pharmacies or [])
        )
    if not offline_prices:
        offline_prices = "- No offline pharmacy price data found."
    price_engine = "Browser Use live web search" if order.online_price_options and order.online_price_options[0].source != "Cost Plus Drugs public API" else "public API fallback"
    return (
        "CareLoop Pharmacy Assistant\n\n"
        f"Need: {order.user_need}\n"
        f"Address area: {order.address_hint}\n"
        f"Location source: {order.location_source}\n\n"
        f"Price engine: {price_engine}\n"
        "Price comparison found:\n"
        "Online prices:\n"
        f"{online_prices}\n\n"
        "Offline pickup options:\n"
        f"{offline_prices}\n\n"
        "Recommended OTC option:\n"
        f"- Item: {order.product.name} ({order.product.active_ingredient} {order.product.strength})\n"
        f"- Package: {order.product.package_size}\n"
        f"- Quantity: {order.quantity}\n"
        f"- Best online quoted subtotal found: {order.subtotal_usd}\n"
        f"- Price source: {order.product.price_source}\n"
        f"- Provider: {order.product.provider}\n"
        f"- Availability: {order.product.availability}\n\n"
        f"Why this option: {order.product.reason}\n\n"
        f"Other options considered:\n{alternatives}\n\n"
        f"Nearby pharmacies from OpenStreetMap:\n{locations}\n\n"
        f"Checkout handoff: {order.product.checkout_url}\n\n"
        f"CareLoop service fee: {order.payment_quote.amount} FET via {order.payment_quote.payment_method}\n"
        f"Payment reference: {order.payment_quote.reference}\n\n"
        "After FET payment, I create the CareLoop order record and return the checkout handoff. "
        "The final product purchase, shipping address, and card payment happen on the provider checkout page.\n\n"
        "Offline price note: I only show store prices when the live browser search can read them. "
        "Otherwise I show real nearby pharmacy locations for pickup planning.\n\n"
        f"Safety note: {order.product.safety_note}"
    )


def otc_order_paid_result(request: CareRequest, order: PharmacyOrderQuote) -> CareResult:
    return CareResult(
        case_id=request.case_id,
        agent_name=PHARMACY_ASSISTANT_AGENT_NAME,
        status="order_ready_for_checkout",
        summary=(
            f"OTC order record created for {order.quantity} x {order.product.name}. "
            f"Real quoted subtotal is {order.subtotal_usd} from {order.product.price_source}. Complete fulfillment here: "
            f"{order.product.checkout_url}"
        ),
        next_actions=[
            "Open the provider checkout link to confirm shipping and product payment.",
            "Review the OTC Drug Facts label and ask a pharmacist if unsure.",
            "Notify caregiver after checkout is completed.",
        ],
        timeline_events=[
            "OTC order quote prepared",
            f"Payment completed: {order.payment_quote.amount} FET",
            "Checkout handoff created",
        ],
    )


def otc_order_unpaid_result(request: CareRequest, reason: str) -> CareResult:
    return CareResult(
        case_id=request.case_id,
        agent_name=PHARMACY_ASSISTANT_AGENT_NAME,
        status="payment_required",
        summary=f"OTC order quote is ready, but the CareLoop service fee was not paid. Reason: {reason}",
        next_actions=[
            f"Approve the {OTC_ORDER_SERVICE_FEE_FET} FET service fee to create the order record.",
            "Use the checkout handoff only after reviewing the OTC label and caregiver needs.",
        ],
        timeline_events=["Payment requested for OTC order"],
    )


def _mock_pending_prescription(request: CareRequest) -> tuple[str, str]:
    if request.context:
        medication = request.context.get("medication")
        dosage = request.context.get("dosage")
        if medication:
            return str(medication), str(dosage or "dose pending pharmacy confirmation")

    explicit = infer_medication_if_present(request.text)
    if explicit is not None:
        return explicit

    normalized = normalize_text(request.text)
    if "mom" in normalized or "mother" in normalized:
        return "Lisinopril", "10 mg"
    if "dad" in normalized or "father" in normalized:
        return "Atorvastatin", "20 mg"
    if "delivery" in normalized or "deliver" in normalized:
        return "Atorvastatin", "20 mg"
    return "Metformin", "500 mg"


def _status_for_request(text: str, medication: str, preference: str, monitor_tick: int = 0) -> tuple[str, str, str | None]:
    normalized = normalize_text(text)
    med_key = medication.lower()
    wants_delivery = "delivery" in preference.lower() or "deliver" in normalized

    if "insurance" in normalized:
        return "action_needed", "Insurance issue needs review", "Confirm insurance or ask pharmacy for cash price."
    if "out of stock" in normalized or "shortage" in normalized:
        return "delayed", "Out of stock today", "Ask the pharmacy to transfer or order the medication."
    if "clarification" in normalized or "prescriber clarification" in normalized:
        return "action_needed", "Pharmacy needs prescriber clarification", "The pharmacy is waiting for the doctor's office."

    if monitor_tick >= 2:
        return (
            "ready_for_delivery" if wants_delivery else "ready_for_pickup",
            "Ready now",
            None,
        )
    if monitor_tick == 1:
        return "in_progress", "Pharmacist verification in progress; estimated ready in 20 minutes", None

    if med_key == "atorvastatin":
        return (
            "ready_for_delivery" if wants_delivery else "ready_for_pickup",
            "Ready now",
            None,
        )
    if med_key == "metformin":
        return "in_progress", "Received by pharmacy; estimated ready in 45 minutes", None
    if med_key == "lisinopril":
        return "delayed", "Delayed; estimated later today", "Pharmacy is confirming stock."
    if med_key == "albuterol":
        return "action_needed", "Needs pharmacist review before release", "Ask whether the inhaler is ready and whether counseling is required."

    return "received", "Prescription received; status check pending", None


def _friendly_status(status: str, preference: str) -> tuple[str | None, str | None, str]:
    wants_delivery = "delivery" in preference.lower()
    if status == "ready_for_pickup":
        return "Today before 8 PM", None, "Bring ID and insurance card if the pharmacy requests it."
    if status == "ready_for_delivery":
        return None, "Delivery today, 6-8 PM", "Keep phone nearby in case the courier or pharmacy calls."
    if status == "in_progress":
        return None, None, "No action needed yet. I can keep checking and notify you when it is ready."
    if status == "delayed":
        return None, None, "A delay may affect when the patient can start the medication. Keep the prescriber in the loop if urgent."
    if status == "action_needed":
        return None, None, "This needs pharmacy or prescriber action before the medicine can be released."
    return None, None, "I can keep checking this status until it changes."


def build_pharmacy_fulfillment_status(
    request: CareRequest,
    *,
    monitor_tick: int = 0,
) -> PharmacyFulfillmentStatus:
    medication, dosage = _mock_pending_prescription(request)
    location = value_from_context(request, "location", "Los Angeles, CA")
    preference = value_from_context(request, "preference", "pickup")
    pharmacy_name = value_from_context(request, "pharmacy_name", _infer_pharmacy_name(request.text))
    status, eta, action_needed = _status_for_request(request.text, medication, preference, monitor_tick)
    pickup_window, delivery_window, senior_note = _friendly_status(status, preference)
    reference = f"careloop-pharmacy-monitor-{request.case_id}-{uuid4().hex[:8]}"
    quote = PaymentQuote(
        case_id=request.case_id,
        service_name="CareLoop Pharmacy Assistant Monitor",
        amount=PHARMACY_SERVICE_FEE_FET,
        reference=reference,
    )

    return PharmacyFulfillmentStatus(
        case_id=request.case_id,
        medication=medication,
        dosage=dosage,
        pharmacy_name=pharmacy_name,
        location=location,
        preference=preference,
        status=status,
        eta=eta,
        pickup_window=pickup_window,
        delivery_window=delivery_window,
        action_needed=action_needed,
        senior_note=senior_note,
        last_checked="mock pharmacy adapter just now",
        next_check_minutes=None if status.startswith("ready") else PHARMACY_MONITOR_CHECK_MINUTES,
        payment_quote=quote,
    )


def format_pharmacy_fulfillment_preview(status: PharmacyFulfillmentStatus) -> str:
    ready_text = "Yes" if status.status.startswith("ready") else "Not yet"
    lines = [
        "CareLoop Pharmacy Assistant",
        "",
        f"Prescription found: {status.medication} {status.dosage}",
        f"Pharmacy: {status.pharmacy_name}",
        f"Location: {status.location}",
        f"Preference: {status.preference}",
        "",
        f"Ready now: {ready_text}",
        f"Status: {status.eta}",
    ]
    if status.pickup_window:
        lines.append(f"Pickup window: {status.pickup_window}")
    if status.delivery_window:
        lines.append(f"Delivery window: {status.delivery_window}")
    if status.action_needed:
        lines.append(f"Action needed: {status.action_needed}")
    lines.extend(
        [
            f"Last checked: {status.last_checked}",
            "",
            f"Senior safety note: {status.senior_note}",
            "",
            f"Active monitoring fee: {status.payment_quote.amount} FET via {status.payment_quote.payment_method}",
            f"Payment reference: {status.payment_quote.reference}",
            "Paid monitoring keeps checking automatically and informs you when the prescription is ready or needs action.",
        ]
    )
    return "\n".join(lines)


def pharmacy_monitoring_result(
    request: CareRequest,
    status: PharmacyFulfillmentStatus,
) -> CareResult:
    if status.status.startswith("ready"):
        summary = (
            f"Yes, {status.medication} {status.dosage} is ready at {status.pharmacy_name}. "
            f"{status.pickup_window or status.delivery_window or status.eta}. {status.senior_note}"
        )
        timeline_status = "Prescription ready"
    else:
        summary = (
            f"I started active monitoring for {status.medication} {status.dosage} at {status.pharmacy_name}. "
            f"Current status: {status.eta}. I will check again every "
            f"{status.next_check_minutes or PHARMACY_MONITOR_CHECK_MINUTES} minutes in the demo monitor."
        )
        timeline_status = "Pharmacy monitoring started"

    return CareResult(
        case_id=request.case_id,
        agent_name=PHARMACY_ASSISTANT_AGENT_NAME,
        status="completed" if status.status.startswith("ready") else "monitoring",
        summary=summary,
        next_actions=[
            "Notify the patient or caregiver when the status changes.",
            "Confirm pharmacist counseling before the patient starts a new medication.",
            "Escalate if the pharmacy reports an insurance issue, stock delay, or prescriber clarification.",
        ],
        timeline_events=[
            "Pharmacy status checked",
            f"Payment completed: {status.payment_quote.amount} FET",
            timeline_status,
        ],
    )


def pharmacy_status_update_result(
    request: CareRequest,
    status: PharmacyFulfillmentStatus,
) -> CareResult:
    return CareResult(
        case_id=request.case_id,
        agent_name=PHARMACY_ASSISTANT_AGENT_NAME,
        status=status.status,
        summary=(
            f"Pharmacy update: {status.medication} {status.dosage} at {status.pharmacy_name} is "
            f"{status.eta}. {status.pickup_window or status.delivery_window or status.senior_note}"
        ),
        next_actions=[
            "Tell the patient or caregiver directly.",
            "Confirm final medication instructions with the pharmacist.",
        ],
        timeline_events=["Auto-check completed", f"Status changed: {status.status}"],
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
        agent_name=PHARMACY_ASSISTANT_AGENT_NAME,
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
        agent_name=PHARMACY_ASSISTANT_AGENT_NAME,
        status="payment_required",
        summary=(
            "Pharmacy status can be checked once, but active automatic monitoring "
            f"is held until the CareLoop Pharmacy Assistant fee is paid. Reason: {reason}"
        ),
        next_actions=[
            f"Approve the {PHARMACY_SERVICE_FEE_FET} FET service fee to monitor until the prescription is ready.",
            "Reject payment to receive only one-time pharmacy safety guidance.",
        ],
        timeline_events=["Payment requested for pharmacy monitoring"],
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


def result_from_extracted_prescription(
    request: PrescriptionDocumentRequest,
    extracted: ExtractedPrescription,
) -> CareResult:
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


def explain_prescription_document(request: PrescriptionDocumentRequest) -> CareResult:
    return result_from_extracted_prescription(request, extract_prescription_text(request))


def is_appointment_intent(text: str) -> bool:
    normalized = normalize_text(text)
    action_terms = [
        "appointment",
        "doctor",
        "provider",
        "physician",
        "clinic",
        "book",
        "schedule",
        "find",
        "see a",
        "primary care",
        "urgent care",
        "dermatologist",
        "cardiologist",
        "dentist",
        "mri",
        "scan",
        "imaging",
        "radiology",
        "xray",
        "x-ray",
    ]
    return any(term in normalized for term in action_terms)


def build_appointment_payment_quote(request: CareRequest) -> PaymentQuote:
    return PaymentQuote(
        case_id=request.case_id,
        service_name="CareLoop Appointment Search",
        amount=APPOINTMENT_SERVICE_FEE_FET,
        reference=f"careloop-appointment-search-{request.case_id}-{uuid4().hex[:8]}",
    )


def build_appointment_search_quote(
    request: CareRequest,
    payment_quote: PaymentQuote | None = None,
) -> AppointmentSearchQuote:
    context = request.context or {}
    specialty = str(context.get("specialty") or infer_appointment_specialty(request.text))
    location = str(context.get("location") or infer_location(request.text))
    insurance = context.get("insurance")
    insurance = str(insurance) if insurance else infer_insurance(request.text)
    urgency = str(context.get("urgency") or infer_appointment_urgency(request.text))
    options, data_sources = appointment_options(specialty, location, insurance, urgency)
    selected = options[0] if options else None
    quote = payment_quote or build_appointment_payment_quote(request)
    return AppointmentSearchQuote(
        case_id=request.case_id,
        specialty=specialty,
        location=location,
        insurance=insurance,
        urgency=urgency,
        options=options,
        selected_option=selected,
        data_sources=data_sources,
        status="options_ready" if options else "no_options_found",
        payment_quote=quote,
    )


def format_appointment_payment_prompt(request: CareRequest, quote: PaymentQuote) -> str:
    specialty = infer_appointment_specialty(request.text)
    location = infer_location(request.text)
    insurance = infer_insurance(request.text)
    insurance_line = f"\nInsurance preference: {insurance}" if insurance else ""
    imaging_note = (
        "\n\nMRI note: many imaging centers require a clinician order or referral before scheduling."
        if specialty == "imaging center"
        else ""
    )
    return (
        "CareLoop Appointment Assistant\n\n"
        f"I can run a live appointment search for {specialty} near {location}.{insurance_line}\n\n"
        "Service fee required before I search live booking pages and public provider records:\n"
        f"- Amount: {quote.amount} {quote.currency}\n"
        f"- Method: {quote.payment_method}\n"
        f"- Reference: {quote.reference}\n\n"
        "After payment, I will show the real providers/booking links I can verify, visible availability or cost "
        "when published, and the safest next step. I will not claim a booking is confirmed unless a real booking API confirms it."
        f"{imaging_note}"
    )


def _appointment_context_note(search: AppointmentSearchQuote) -> str:
    if search.specialty == "imaging center":
        return (
            "MRI/imaging note: many centers require a clinician order, referral, or insurance authorization. "
            "If knee pain is severe, swollen, or follows an injury, consider urgent care or an orthopedic/sports medicine visit."
        )
    if search.specialty in {"orthopedic surgery", "sports medicine"}:
        return "Knee-pain note: bring medication list, symptom timeline, injury details, and any prior imaging."
    return "Prep note: bring ID, insurance card, medication list, symptom notes, and caregiver contact if helpful."


def _caregiver_appointment_summary(search: AppointmentSearchQuote) -> str:
    selected = search.selected_option
    if not selected:
        return "Caregiver update: appointment search completed, but no clear booking option was found yet."
    return (
        f"Caregiver update: CareLoop found a {search.specialty} option near {search.location}: "
        f"{selected.provider_name}. Book/check: {selected.booking_url}. "
        f"Availability: {selected.earliest_available}. Cost: {selected.estimated_cost}."
    )


def format_appointment_search_preview(search: AppointmentSearchQuote) -> str:
    sources = ", ".join(search.data_sources) or "no source available"
    if search.options:
        option_lines = "\n".join(
            (
                f"{index}. {option.provider_name}\n"
                f"   Specialty: {option.specialty}\n"
                f"   Where: {option.location}\n"
                f"   Availability: {option.earliest_available}\n"
                f"   Cost: {option.estimated_cost}\n"
                f"   Phone: {option.phone or 'not published'}\n"
                f"   Link: {option.booking_url}"
            )
            for index, option in enumerate(search.options[:3], start=1)
        )
    else:
        option_lines = "No real appointment options were found for this search right now."

    selected = search.selected_option
    selected_text = (
        f"{selected.provider_name}: open {selected.booking_url}"
        if selected
        else "No selected option yet"
    )
    insurance = search.insurance or "not specified"
    return (
        "CareLoop Appointment Assistant\n\n"
        f"Need: {search.specialty}\n"
        f"Location: {search.location}\n"
        f"Insurance: {insurance}\n"
        f"Urgency: {search.urgency}\n\n"
        f"Real options:\n{option_lines}\n\n"
        f"Best next step: {selected_text}\n\n"
        f"{_appointment_context_note(search)}\n\n"
        f"{_caregiver_appointment_summary(search)}\n\n"
        f"Sources: {sources}\n"
        "Cost note: exact patient cost is often hidden until insurance or cash-pay details are checked on the booking page.\n"
        "Safety note: appointment logistics only, not diagnosis. For emergency symptoms, call emergency services."
    )


def appointment_paid_result(request: CareRequest, search: AppointmentSearchQuote) -> CareResult:
    selected = search.selected_option
    if selected:
        summary = (
            f"Appointment search completed for {search.specialty} near {search.location}. "
            f"Best handoff: {selected.provider_name}. Book/check here: {selected.booking_url}"
        )
    else:
        summary = f"Appointment search completed for {search.specialty} near {search.location}, but no live options were found."
    return CareResult(
        case_id=request.case_id,
        agent_name=APPOINTMENT_AGENT_NAME,
        status="booking_handoff_ready" if selected else "no_options_found",
        summary=summary,
        next_actions=[
            "Open the booking link to choose a slot and enter required patient details.",
            "Confirm insurance, cash price, and cancellation policy before final booking.",
            "Notify caregiver after the appointment is booked.",
        ],
        timeline_events=[
            "Appointment search paid",
            "Real provider options searched",
            "Booking handoff prepared",
        ],
    )


def appointment_unpaid_result(request: CareRequest, reason: str) -> CareResult:
    return CareResult(
        case_id=request.case_id,
        agent_name=APPOINTMENT_AGENT_NAME,
        status="payment_required",
        summary=f"Appointment search is ready to run, but the CareLoop service fee was not paid. Reason: {reason}",
        next_actions=[
            f"Approve the {APPOINTMENT_SERVICE_FEE_FET} FET service fee to run the live search.",
            "Reject payment if you do not want CareLoop to search booking pages/provider records.",
        ],
        timeline_events=["Payment requested for appointment search"],
    )


def book_appointment(request: CareRequest) -> CareResult:
    if not (request.context or {}).get("live_search"):
        specialty = infer_appointment_specialty(request.text)
        location = infer_location(request.text)
        return CareResult(
            case_id=request.case_id,
            agent_name=APPOINTMENT_AGENT_NAME,
            status="payment_required",
            summary=(
                f"The appointment assistant can run a paid live search for {specialty} near {location}. "
                "Use the appointment specialist directly in ASI:One to pay the FET service fee and get real provider links."
            ),
            next_actions=[
                f"Ask {APPOINTMENT_AGENT_NAME} for the appointment search.",
                "Approve the FET service fee in chat.",
                "Use the returned booking handoff link to confirm the final appointment.",
            ],
            timeline_events=["Appointment specialist identified", "Payment required for live appointment search"],
        )

    search = build_appointment_search_quote(request)
    summary = format_appointment_search_preview(search)
    return CareResult(
        case_id=request.case_id,
        agent_name=APPOINTMENT_AGENT_NAME,
        status="booking_handoff_ready" if search.selected_option else "no_options_found",
        summary=summary,
        next_actions=[
            "Open the booking handoff link to choose and confirm a real slot.",
            "Confirm insurance and cost on the provider booking page.",
            "Notify caregiver after final confirmation.",
        ],
        timeline_events=["Appointment options searched", "Booking handoff prepared"],
    )


def _infer_caregiver_channel(text: str, context: dict | None) -> str:
    if context and context.get("channel"):
        return str(context["channel"]).lower()
    normalized = normalize_text(text)
    if any(term in normalized for term in ["email", "mail"]):
        return "email"
    return "sms"


def _infer_caregiver_recipient(text: str, context: dict | None) -> str:
    if context and context.get("caregiver"):
        return str(context["caregiver"])
    normalized = normalize_text(text)
    for label in ["daughter", "son", "wife", "husband", "sister", "brother", "mom", "dad"]:
        if label in normalized:
            return label
    return "family caregiver"


def _infer_patient_name(context: dict | None) -> str:
    if context and context.get("patient_name"):
        return str(context["patient_name"])
    return "the patient"


def _infer_notification_urgency(text: str, context: dict | None) -> str:
    if context and context.get("urgency"):
        return str(context["urgency"]).lower()
    normalized = normalize_text(text)
    if any(term in normalized for term in ["emergency", "911", "cannot breathe", "chest pain", "severe", "urgent"]):
        return "urgent"
    if any(
        term in normalized
        for term in ["payment", "checkout", "confirm", "pickup", "appointment", "booked", "ready", "missed", "refill"]
    ):
        return "action_needed"
    return "info"


def _infer_care_event(text: str) -> str:
    normalized = normalize_text(text)
    if any(term in normalized for term in ["pharmacy", "otc", "medicine", "medication", "prescription", "checkout"]):
        return "medication/pharmacy update"
    if any(term in normalized for term in ["appointment", "doctor", "clinic", "visit"]):
        return "appointment update"
    if any(term in normalized for term in ["reminder", "missed", "taken", "dose"]):
        return "medication reminder update"
    if any(term in normalized for term in ["emergency", "urgent", "chest pain", "cannot breathe"]):
        return "urgent care alert"
    return "care coordination update"


def _shorten_event_text(text: str, limit: int = 240) -> str:
    cleaned = " ".join(text.replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _caregiver_message(
    *,
    caregiver: str,
    patient_name: str,
    event: str,
    urgency: str,
    channel: str,
    source_text: str,
) -> str:
    brief = _shorten_event_text(source_text, 220 if channel == "sms" else 420)
    if urgency == "urgent":
        return (
            f"URGENT: Please check on {patient_name} now. CareLoop flagged this as an urgent care alert: "
            f"{brief} If symptoms may be serious, call emergency services or seek emergency care now."
        )
    if channel == "email":
        return (
            f"Subject: CareLoop {event} for {patient_name}\n\n"
            f"Hi {caregiver},\n\n"
            f"CareLoop has an update for {patient_name}: {brief}\n\n"
            "Suggested next step: please confirm you saw this and help with any pickup, checkout, "
            "transportation, or medication questions if needed.\n\n"
            "Safety note: this is coordination support, not medical advice. Confirm medication or clinical "
            "instructions with a pharmacist or clinician."
        )
    return (
        f"CareLoop update for {caregiver}: {patient_name} has a {event}. {brief} "
        "Please confirm you saw this and help with the next step if needed. "
        "Confirm medication or clinical instructions with a pharmacist or clinician."
    )


def notify_caregiver(request: CareRequest) -> CareResult:
    context = request.context or {}
    caregiver = _infer_caregiver_recipient(request.text, context)
    patient_name = _infer_patient_name(context)
    channel = _infer_caregiver_channel(request.text, context)
    urgency = _infer_notification_urgency(request.text, context)
    event = _infer_care_event(request.text)
    message = _caregiver_message(
        caregiver=caregiver,
        patient_name=patient_name,
        event=event,
        urgency=urgency,
        channel=channel,
        source_text=request.text,
    )
    summary = (
        f"Caregiver notification drafted\n"
        f"Recipient: {caregiver}\n"
        f"Channel: {channel}\n"
        f"Urgency: {urgency}\n"
        f"Event: {event}\n\n"
        f"{message}"
    )
    return CareResult(
        case_id=request.case_id,
        agent_name="careloop-caregiver-notifier",
        status=urgency,
        summary=summary,
        next_actions=[
            f"Send {channel}-style caregiver update.",
            "Ask caregiver to confirm receipt.",
            "Escalate if the caregiver does not respond and the update is urgent.",
        ],
        timeline_events=[f"Caregiver notification drafted: {urgency}"],
    )


def notify_caregiver_from_result(result: CareResult, context: dict | None = None) -> CareResult:
    request = CareRequest(
        case_id=result.case_id,
        user_id="careloop-system",
        text=(
            f"Source agent: {result.agent_name}. Status: {result.status}. "
            f"Summary: {result.summary}. Next actions: {'; '.join(result.next_actions)}"
        ),
        context=context,
    )
    return notify_caregiver(request)


def triage_request(request: CareRequest) -> CareResult:
    normalized = normalize_text(request.text)
    emergency_reason = triage_emergency_reason(request.text)
    if emergency_reason:
        return CareResult(
            case_id=request.case_id,
            agent_name=TRIAGE_AGENT_NAME,
            status="urgent_escalation",
            summary=(
                f"This may be an emergency ({emergency_reason}). CareLoop should not automate this request. "
                "Call 911 or local emergency services immediately."
            ),
            next_actions=[
                "Call emergency services now.",
                "Notify caregiver immediately.",
                "Do not wait for an agent workflow if symptoms are severe or worsening.",
            ],
            timeline_events=["Emergency language detected", "Automation stopped"],
        )

    decision = triage_route(request.text)
    route = decision["route"]
    confidence = decision["confidence"]
    rationale = decision["rationale"]

    return CareResult(
        case_id=request.case_id,
        agent_name=TRIAGE_AGENT_NAME,
        status="needs_clarification" if route == "clarify" else "completed",
        summary=(
            f"Route: {route}\n"
            f"Confidence: {confidence}\n"
            f"Reason: {rationale}"
        ),
        next_actions=triage_next_actions(route),
        timeline_events=["Triage completed", f"Route selected: {route}"],
    )


def triage_emergency_reason(text: str) -> str | None:
    normalized = normalize_text(text)
    emergency_terms = {
        "chest pain": "chest pain",
        "pressure in chest": "possible chest pain",
        "stroke": "possible stroke symptoms",
        "face drooping": "possible stroke symptoms",
        "slurred speech": "possible stroke symptoms",
        "can't breathe": "trouble breathing",
        "cannot breathe": "trouble breathing",
        "trouble breathing": "trouble breathing",
        "severe allergic reaction": "possible severe allergic reaction",
        "throat swelling": "possible severe allergic reaction",
        "severe bleeding": "severe bleeding",
        "unconscious": "unconsciousness",
        "fainting": "fainting",
        "confused suddenly": "sudden confusion",
        "worst headache": "severe sudden headache",
        "suicidal": "self-harm risk",
        "overdose": "possible overdose",
    }
    for term, reason in emergency_terms.items():
        if term in normalized:
            return reason
    return None


def triage_route(text: str) -> dict[str, str]:
    normalized = normalize_text(text)
    if _looks_like_caregiver_request(normalized):
        return {
            "route": "careloop-caregiver-notifier",
            "confidence": "high",
            "rationale": "The user wants a family/caregiver message or update drafted.",
        }
    if is_pharmacy_status_intent(text):
        return {
            "route": "careloop-orchestrator",
            "confidence": "medium",
            "rationale": "Prescription readiness needs patient/pharmacy context owned by the orchestrator flow.",
        }
    if _looks_like_prescription_request(normalized):
        return {
            "route": "careloop-prescription-explainer",
            "confidence": "high",
            "rationale": "The user is asking about a prescription label, medication instructions, or uploaded prescription document.",
        }
    if is_otc_order_intent(text):
        return {
            "route": PHARMACY_ASSISTANT_AGENT_NAME,
            "confidence": "high",
            "rationale": "The user is asking to find, compare, or order over-the-counter medicine.",
        }
    if is_appointment_intent(text):
        return {
            "route": APPOINTMENT_AGENT_NAME,
            "confidence": "high",
            "rationale": "The user needs a doctor, clinic, imaging, or appointment booking/search handoff.",
        }
    if _looks_like_adherence_request(normalized):
        return {
            "route": "careloop-adherence",
            "confidence": "high",
            "rationale": "The user is asking about reminders, missed doses, or a medication schedule.",
        }
    return {
        "route": "clarify",
        "confidence": "low",
        "rationale": "The request does not clearly match prescription, pharmacy, appointment, caregiver, or adherence support.",
    }


def triage_next_actions(route: str) -> list[str]:
    actions = {
        "careloop-prescription-explainer": [
            "Route to @careloop-prescription-explainer.",
            "Ask the user to upload/paste the prescription label if not already provided.",
        ],
        PHARMACY_ASSISTANT_AGENT_NAME: [
            "Start the OTC search/order flow.",
            "Ask for the FET service fee before the live price search.",
        ],
        APPOINTMENT_AGENT_NAME: [
            "Start the appointment/provider search flow.",
            "Ask for the FET service fee before the live appointment/provider search.",
        ],
        "careloop-caregiver-notifier": [
            "Route to @careloop-caregiver-notifier.",
            "Include recipient, channel, urgency, and care event if known.",
        ],
        "careloop-adherence": [
            "Route to @careloop-adherence.",
            "Collect medication name, schedule, and caregiver escalation preference.",
        ],
        "careloop-orchestrator": [
            "Route to the CareLoop orchestrator when patient/pharmacy context is required.",
            "Avoid asking the patient to know hidden e-prescription details.",
        ],
        "clarify": [
            "Ask one short clarifying question.",
            "Offer prescription, OTC pharmacy, appointment, caregiver update, or reminder routing choices.",
        ],
    }
    return actions.get(route, [f"Route case to {route}.", "Keep caregiver in the loop."])


def _looks_like_caregiver_request(normalized: str) -> bool:
    return any(term in normalized for term in ["tell my", "text my", "message my", "send my", "caregiver", "daughter", "son"]) and any(
        term in normalized for term in ["update", "tell", "text", "message", "send", "notify", "write"]
    )


def _looks_like_prescription_request(normalized: str) -> bool:
    return any(
        term in normalized
        for term in [
            "prescription",
            "rx",
            "pill bottle",
            "label",
            "dosage",
            "dose",
            "directions",
            "side effects",
            "interactions",
            "take this medication",
            "what is this medication",
            "explain this medicine",
        ]
    )


def _looks_like_adherence_request(normalized: str) -> bool:
    return any(term in normalized for term in ["remind", "reminder", "missed dose", "missed my", "schedule my medicine", "took my pill", "taken"])


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
            "Invoke careloop-pharmacy-assistant for paid prescription status monitoring.",
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
