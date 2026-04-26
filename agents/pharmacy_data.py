from copy import deepcopy
from math import atan2, cos, radians, sin, sqrt
import httpx

from models import OTCProduct


COST_PLUS_URL = "https://us-central1-costplusdrugs-publicapi.cloudfunctions.net/main"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "CareLoopHackathon/0.1 (educational demo)"


COST_PLUS_LOOKUPS = {
    "Loratadine": {"medication_name": "loratadine", "strength": "10mg", "quantity_units": "30"},
    "Ibuprofen": {"medication_name": "ibuprofen", "strength": "200mg", "quantity_units": "30"},
    "Famotidine": {"medication_name": "famotidine", "strength": "20mg", "quantity_units": "30"},
    "Aspirin": {"medication_name": "aspirin", "strength": "81mg", "quantity_units": "30"},
}


def _request_json(method: str, url: str, **kwargs):
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    response = httpx.request(method, url, headers=headers, timeout=12, **kwargs)
    response.raise_for_status()
    return response.json()


def enrich_product_with_costplus(product: OTCProduct) -> OTCProduct:
    lookup = COST_PLUS_LOOKUPS.get(product.active_ingredient)
    if not lookup:
        return product

    try:
        data = _request_json("GET", COST_PLUS_URL, params=lookup)
    except Exception:
        return product

    results = data.get("results") or []
    if not results:
        return product

    record = results[0]
    enriched = deepcopy(product)
    enriched.name = f"{record.get('medication_name', product.active_ingredient)} ({record.get('brand_name', product.name)} generic)"
    enriched.strength = record.get("strength", product.strength)
    quote_units = record.get("requested_quote_units") or lookup["quantity_units"]
    form = record.get("form") or "units"
    enriched.package_size = f"{quote_units} {form.lower()}s"
    enriched.unit_price_usd = record.get("requested_quote") or product.unit_price_usd
    enriched.availability = "Real Cost Plus Drugs quote; final checkout may add shipping/taxes"
    enriched.provider = "Cost Plus Drugs"
    enriched.checkout_url = record.get("url") or product.checkout_url
    enriched.price_source = "Cost Plus Drugs public API"
    return enriched


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.8
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return radius_miles * 2 * atan2(sqrt(a), sqrt(1 - a))


def geocode_address(address: str) -> tuple[float, float] | None:
    try:
        data = _request_json(
            "GET",
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
        )
    except Exception:
        return None

    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def nearby_pharmacies(address: str, limit: int = 3) -> list[str]:
    coords = geocode_address(address)
    if coords is None:
        return []
    lat, lon = coords
    query = f"""
[out:json][timeout:15];
(
  node["amenity"="pharmacy"](around:5000,{lat},{lon});
  way["amenity"="pharmacy"](around:5000,{lat},{lon});
  relation["amenity"="pharmacy"](around:5000,{lat},{lon});
);
out center tags {max(limit * 4, 12)};
"""
    try:
        data = _request_json("POST", OVERPASS_URL, content=query)
    except Exception:
        return []

    pharmacies: list[tuple[float, str]] = []
    seen: set[str] = set()
    for element in data.get("elements", []):
        tags = element.get("tags") or {}
        name = tags.get("name") or tags.get("brand")
        if not name:
            continue
        item_lat = element.get("lat") or (element.get("center") or {}).get("lat")
        item_lon = element.get("lon") or (element.get("center") or {}).get("lon")
        if item_lat is None or item_lon is None:
            continue

        distance = _haversine_miles(lat, lon, float(item_lat), float(item_lon))
        street = " ".join(
            part
            for part in [
                tags.get("addr:housenumber"),
                tags.get("addr:street"),
            ]
            if part
        )
        label = f"{name} - {distance:.1f} mi"
        if street:
            label += f", {street}"
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        pharmacies.append((distance, label))

    pharmacies.sort(key=lambda item: item[0])
    return [label for _, label in pharmacies[:limit]]
