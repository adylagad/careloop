from typing import Any

from uagents import Model


class CareRequest(Model):
    case_id: str
    user_id: str
    text: str
    context: dict[str, Any] | None = None


class CareResult(Model):
    case_id: str
    agent_name: str
    status: str
    summary: str
    next_actions: list[str]
    timeline_events: list[str] | None = None


class PaymentQuote(Model):
    case_id: str
    service_name: str
    amount: str
    currency: str = "FET"
    payment_method: str = "fet_direct"
    reference: str


class PrescriptionDocumentRequest(Model):
    case_id: str
    user_id: str
    document_text: str | None = None
    document_path: str | None = None
    document_uri: str | None = None
    document_base64: str | None = None
    content_type: str | None = None
    patient_context: dict[str, Any] | None = None


class PharmacyOption(Model):
    name: str
    price_usd: str
    availability: str
    eta: str
    fit_score: int
    senior_note: str


class PharmacyRecommendation(Model):
    medication: str
    dosage: str
    location: str
    preference: str
    options: list[PharmacyOption]
    selected_option: PharmacyOption
    payment_quote: PaymentQuote
