import asyncio
from dataclasses import dataclass
import os
import re
from threading import Thread
from urllib.parse import quote_plus

import httpx
from dotenv import load_dotenv

from browser_cache import cached_browser_call
from models import AppointmentOption
from pharmacy_data import USER_AGENT, geocode_address


load_dotenv()

NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
BROWSER_APPOINTMENT_MODEL = os.getenv("APPOINTMENT_BROWSER_MODEL", "gpt-5.4-mini")
BROWSER_APPOINTMENT_LIMIT = int(os.getenv("APPOINTMENT_BROWSER_LIMIT", "5"))

SPECIALTY_KEYWORDS = {
    "imaging center": ["mri", "scan", "imaging", "radiology", "xray", "x-ray", "ct scan", "ultrasound"],
    "primary care": ["primary care", "pcp", "family doctor", "family medicine", "checkup", "annual"],
    "internal medicine": ["internal medicine", "internist"],
    "geriatrics": ["geriatric", "elder", "senior"],
    "dermatology": ["skin", "rash", "dermatology", "dermatologist"],
    "cardiology": ["heart", "cardiology", "cardiologist", "chest follow up"],
    "orthopedic surgery": ["knee", "hip", "shoulder", "orthopedic", "orthopedist", "bone"],
    "sports medicine": ["sports medicine", "sprain", "strain", "joint injury", "knee pain"],
    "ophthalmology": ["eye", "vision", "ophthalmology", "ophthalmologist"],
    "dentistry": ["dental", "tooth", "dentist"],
    "urgent care": ["urgent", "same day", "today", "asap", "walk in"],
}

NPPES_TAXONOMY_BY_SPECIALTY = {
    "imaging center": "Clinic/Center, Radiology",
    "primary care": "Family Medicine",
    "geriatrics": "Geriatric Medicine",
    "sports medicine": "Sports Medicine",
    "urgent care": "Clinic/Center, Urgent Care",
    "dentistry": "Dentist",
}

NPI2_SPECIALTIES = {"imaging center", "urgent care"}


@dataclass
class LocationParts:
    query: str
    city: str | None
    state: str | None


def infer_appointment_specialty(text: str) -> str:
    normalized = " ".join(text.lower().split())
    for specialty, keywords in SPECIALTY_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return specialty
    if any(term in normalized for term in ["doctor", "appointment", "provider", "physician"]):
        return "primary care"
    return "primary care"


def infer_appointment_urgency(text: str) -> str:
    normalized = " ".join(text.lower().split())
    if any(term in normalized for term in ["today", "same day", "urgent", "asap", "this week"]):
        return "soon"
    return "routine"


def infer_insurance(text: str) -> str | None:
    normalized = " ".join(text.lower().split())
    known = ["medicare", "medicaid", "aetna", "blue cross", "blue shield", "cigna", "unitedhealthcare", "kaiser"]
    for item in known:
        if item in normalized:
            return item.title()
    match = re.search(r"\binsurance(?: is|:)?\s+([A-Za-z][A-Za-z &-]{2,40})(?:[.!?]|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip(" .,")
    return None


def infer_location(text: str, default: str = "Los Angeles, CA") -> str:
    match = re.search(
        r"\b(?:near|around|in|at|by)\s+([A-Za-z0-9][A-Za-z0-9 .,'-]{2,70}?)(?:\s+with\b|\s+that\b|\s+for\b|\s+this\b|\s+right now\b|\s+now\b|[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip(" .,")
    return default


def _request_json(method: str, url: str, **kwargs):
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    response = httpx.request(method, url, headers=headers, timeout=15, **kwargs)
    response.raise_for_status()
    return response.json()


def _location_parts(location: str) -> LocationParts:
    parts = [part.strip() for part in location.split(",") if part.strip()]
    city = parts[0] if parts else None
    state = None
    if len(parts) > 1:
        state_candidate = parts[1].split()[0].upper()
        if len(state_candidate) == 2:
            state = state_candidate
    if city and state:
        return LocationParts(query=location, city=city, state=state)

    try:
        data = _request_json(
            "GET",
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "addressdetails": 1, "limit": 1},
        )
    except Exception:
        return LocationParts(query=location, city=city, state=state)

    if not data:
        return LocationParts(query=location, city=city, state=state)
    address = data[0].get("address") or {}
    city = address.get("city") or address.get("town") or address.get("village") or address.get("suburb") or city
    state = (address.get("state_code") or "").upper() or state
    if not state and address.get("state") == "California":
        state = "CA"
    return LocationParts(query=location, city=city, state=state)


def _zocdoc_search_url(specialty: str, location: str, insurance: str | None = None) -> str:
    query = specialty
    if insurance:
        query += f" {insurance}"
    return f"https://www.zocdoc.com/search?query={quote_plus(query)}&address={quote_plus(location)}"


def _healthgrades_search_url(specialty: str, location: str) -> str:
    return f"https://www.healthgrades.com/usearch?what={quote_plus(specialty)}&where={quote_plus(location)}"


def _zocdoc_or_maps_url(specialty: str, location: str, insurance: str | None = None) -> str:
    if specialty.lower() == "imaging center":
        return _maps_search_url("MRI imaging center", location)
    return _zocdoc_search_url(specialty, location, insurance)


def _maps_search_url(provider: str, location: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(provider + ' ' + location)}"


def _provider_name(record: dict) -> str:
    basic = record.get("basic") or {}
    organization = basic.get("organization_name")
    if organization:
        return str(organization).title()
    parts = [
        basic.get("first_name"),
        basic.get("middle_name"),
        basic.get("last_name"),
        basic.get("credential"),
    ]
    return " ".join(str(part).title() for part in parts if part).strip() or "Provider name unavailable"


def _provider_location(record: dict) -> tuple[str, str | None]:
    addresses = record.get("addresses") or []
    selected = None
    for address in addresses:
        if address.get("address_purpose") == "LOCATION":
            selected = address
            break
    selected = selected or (addresses[0] if addresses else {})
    line1 = selected.get("address_1")
    city = selected.get("city")
    state = selected.get("state")
    postal = selected.get("postal_code")
    location = ", ".join(str(part).title() for part in [line1, city, state] if part)
    if postal:
        location = f"{location} {str(postal)[:5]}".strip()
    phone = selected.get("telephone_number")
    return location or "practice location unavailable", phone


def _record_specialty(record: dict, fallback: str) -> str:
    taxonomies = record.get("taxonomies") or []
    for taxonomy in taxonomies:
        desc = taxonomy.get("desc")
        if desc:
            return str(desc)
    return fallback


def nppes_provider_options(specialty: str, location: str, insurance: str | None = None, limit: int = 5) -> list[AppointmentOption]:
    parts = _location_parts(location)
    taxonomy_description = NPPES_TAXONOMY_BY_SPECIALTY.get(specialty.lower(), specialty)
    params = {
        "version": "2.1",
        "enumeration_type": "NPI-2" if specialty.lower() in NPI2_SPECIALTIES else "NPI-1",
        "taxonomy_description": taxonomy_description,
        "limit": max(limit, 5),
        "country_code": "US",
    }
    if parts.city:
        params["city"] = parts.city
    if parts.state:
        params["state"] = parts.state
    if not parts.city and not parts.state:
        params["city"] = "Los Angeles"
        params["state"] = "CA"

    try:
        data = _request_json("GET", NPPES_URL, params=params)
    except Exception:
        return [_appointment_search_handoff_option(specialty, location, insurance)]

    options: list[AppointmentOption] = []
    seen: set[str] = set()
    for record in data.get("results") or []:
        name = _provider_name(record)
        practice_location, phone = _provider_location(record)
        if not practice_location or practice_location == "practice location unavailable":
            practice_location = location
        key = f"{name}|{practice_location}".lower()
        if key in seen:
            continue
        seen.add(key)
        option_specialty = _record_specialty(record, specialty)
        options.append(
            AppointmentOption(
                provider_name=name,
                specialty=option_specialty,
                location=practice_location,
                phone=phone,
                earliest_available="not published in CMS NPPES",
                estimated_cost="not published; depends on insurance/cash policy",
                booking_url=_maps_search_url(name, practice_location),
                profile_url=f"https://npiregistry.cms.hhs.gov/provider-view/{record.get('number')}",
                source="CMS NPPES public provider registry",
                npi=str(record.get("number") or ""),
                notes=(
                    "CMS verifies the public provider record, but it does not publish live appointment slots, "
                    "accepted insurance, or visit price."
                ),
            )
        )
        if len(options) >= limit:
            break

    if not options:
        options.append(_appointment_search_handoff_option(specialty, location, insurance))
    return options


def _appointment_search_handoff_option(
    specialty: str,
    location: str,
    insurance: str | None = None,
) -> AppointmentOption:
    provider_name = (
        "MRI imaging centers near you"
        if specialty.lower() == "imaging center"
        else f"{specialty.title()} booking search"
    )
    return AppointmentOption(
        provider_name=provider_name,
        specialty=specialty,
        location=location,
        earliest_available="check live booking page",
        estimated_cost="shown by booking site when available",
        booking_url=_zocdoc_or_maps_url(specialty, location, insurance),
        profile_url=_healthgrades_search_url(specialty, location),
        source="Zocdoc/Google Maps/Healthgrades search handoff",
        notes="Public provider registry lookup was unavailable or returned no exact rows, so this is a real booking/search handoff.",
    )


async def _fetch_browser_appointment_options_async(
    specialty: str,
    location: str,
    insurance: str | None,
    urgency: str,
) -> list[AppointmentOption]:
    from browser_use_sdk.v3 import AsyncBrowserUse

    client = AsyncBrowserUse()
    insurance_text = insurance or "insurance not specified"
    task = (
        "Find current publicly visible appointment booking options for a patient. "
        "Use real sources such as Zocdoc, Healthgrades, provider websites, Google Business profiles, "
        "urgent care clinic pages, or hospital scheduling pages. Return only options with a real URL. "
        "If a real appointment slot or visit cost is visible, include it exactly. If cost or availability is not visible, "
        "write 'not published'. Do not invent providers, times, or prices. "
        "For MRI/imaging searches, prefer imaging centers, radiology centers, and hospital radiology scheduling pages; "
        "note when a clinician order/referral may be required. "
        f"Specialty/need: {specialty}. Location: {location}. Insurance: {insurance_text}. Urgency: {urgency}. "
        f"Return at most {BROWSER_APPOINTMENT_LIMIT} lines in this exact format: "
        "- Provider — Specialty — Location — Earliest availability — Cost — Booking URL."
    )
    result = await client.run(task, model=BROWSER_APPOINTMENT_MODEL, proxy_country_code="us")
    return parse_browser_appointment_text(str(result.output))


def parse_browser_appointment_text(text: str) -> list[AppointmentOption]:
    options: list[AppointmentOption] = []
    pattern = re.compile(
        r"^\s*[-*]\s*(?P<provider>[^—\n]+?)\s*—\s*"
        r"(?P<specialty>[^—\n]+?)\s*—\s*"
        r"(?P<location>[^—\n]+?)\s*—\s*"
        r"(?P<availability>[^—\n]+?)\s*—\s*"
        r"(?P<cost>[^—\n]+?)\s*—\s*"
        r"(?P<url>https?://\S+)",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        options.append(
            AppointmentOption(
                provider_name=match.group("provider").strip(),
                specialty=match.group("specialty").strip(),
                location=match.group("location").strip(),
                earliest_available=match.group("availability").strip(),
                estimated_cost=match.group("cost").strip(),
                booking_url=match.group("url").rstrip(").,"),
                source="Browser Use live booking search",
                notes="Visible public booking result; re-check on the linked page before entering patient information.",
            )
        )
        if len(options) >= BROWSER_APPOINTMENT_LIMIT:
            break
    return options


def _run_async_in_thread(coro):
    result = None
    error: BaseException | None = None

    def runner():
        nonlocal result, error
        try:
            result = asyncio.run(coro)
        except BaseException as exc:
            error = exc

    thread = Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=int(os.getenv("APPOINTMENT_BROWSER_TIMEOUT_SECONDS", "210")))
    if thread.is_alive() or error is not None:
        return None
    return result or []


def browseruse_appointment_options(
    specialty: str,
    location: str,
    insurance: str | None,
    urgency: str,
) -> list[AppointmentOption]:
    if not os.getenv("BROWSER_USE_API_KEY"):
        return []
    try:
        import browser_use_sdk.v3  # noqa: F401
    except Exception:
        return []

    def loader() -> list[dict] | None:
        options = _run_async_in_thread(
            _fetch_browser_appointment_options_async(specialty, location, insurance, urgency)
        )
        if options is None:
            return None
        return [option.dict() for option in options]

    cached_value, _ = cached_browser_call(
        namespace="appointment-options-v1",
        payload={
            "specialty": specialty,
            "location": location,
            "insurance": insurance or "",
            "urgency": urgency,
            "limit": BROWSER_APPOINTMENT_LIMIT,
            "model": BROWSER_APPOINTMENT_MODEL,
        },
        loader=loader,
    )
    return [AppointmentOption(**item) for item in (cached_value or [])]


def appointment_options(specialty: str, location: str, insurance: str | None, urgency: str) -> tuple[list[AppointmentOption], list[str]]:
    browser_options = browseruse_appointment_options(specialty, location, insurance, urgency)
    if browser_options:
        return browser_options, ["Browser Use live booking search"]
    nppes_options = nppes_provider_options(specialty, location, insurance)
    sources = ["CMS NPPES public provider registry", "OpenStreetMap/Nominatim location lookup"]
    if nppes_options:
        sources.append("Zocdoc/Google Maps/Healthgrades booking handoff links")
    return nppes_options, sources
