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


class PharmacyFulfillmentStatus(Model):
    case_id: str
    medication: str
    dosage: str
    pharmacy_name: str
    location: str
    preference: str
    status: str
    eta: str
    pickup_window: str | None = None
    delivery_window: str | None = None
    action_needed: str | None = None
    senior_note: str
    last_checked: str
    next_check_minutes: int | None = None
    payment_quote: PaymentQuote


class OTCProduct(Model):
    name: str
    category: str
    active_ingredient: str
    strength: str
    package_size: str
    unit_price_usd: str
    availability: str
    provider: str
    checkout_url: str
    fit_score: int
    reason: str
    safety_note: str
    price_source: str = "mock catalog"


class OTCPriceOption(Model):
    product_name: str
    price_usd: str
    merchant: str
    fulfillment: str
    source: str
    url: str | None = None
    notes: str | None = None


class PharmacyOrderQuote(Model):
    case_id: str
    product: OTCProduct
    alternatives: list[OTCProduct]
    quantity: int
    subtotal_usd: str
    fulfillment_method: str
    address_hint: str
    user_need: str
    nearby_pharmacies: list[str] | None = None
    location_source: str | None = None
    online_price_options: list[OTCPriceOption] | None = None
    offline_price_options: list[OTCPriceOption] | None = None
    status: str
    payment_quote: PaymentQuote


class AppointmentOption(Model):
    provider_name: str
    specialty: str
    location: str
    phone: str | None = None
    earliest_available: str = "availability not published"
    estimated_cost: str = "cost not published"
    booking_url: str
    profile_url: str | None = None
    source: str
    npi: str | None = None
    notes: str | None = None


class AppointmentSearchQuote(Model):
    case_id: str
    specialty: str
    location: str
    insurance: str | None = None
    urgency: str = "routine"
    options: list[AppointmentOption]
    selected_option: AppointmentOption | None = None
    data_sources: list[str]
    status: str
    payment_quote: PaymentQuote
