from copy import deepcopy
import asyncio
from math import atan2, cos, radians, sin, sqrt
import os
from threading import Thread
import httpx
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from models import OTCPriceOption, OTCProduct


load_dotenv()

COST_PLUS_URL = "https://us-central1-costplusdrugs-publicapi.cloudfunctions.net/main"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "CareLoopHackathon/0.1 (educational demo)"
BROWSER_PRICE_MODEL = os.getenv("PHARMACY_BROWSER_PRICE_MODEL", "gpt-5.4-mini")
BROWSER_PRICE_LIMIT = int(os.getenv("PHARMACY_BROWSER_PRICE_LIMIT", "6"))


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


def costplus_price_option(product: OTCProduct) -> OTCPriceOption | None:
    if not product.unit_price_usd.startswith("$"):
        return None
    return OTCPriceOption(
        product_name=product.name,
        price_usd=product.unit_price_usd,
        merchant=product.provider,
        fulfillment="online checkout",
        source=product.price_source,
        url=product.checkout_url,
        notes=product.availability,
    )


class _BrowserPriceOption(BaseModel):
    product_name: str = Field(description="Exact product name shown on the site")
    price_usd: str = Field(description="Displayed consumer price, formatted like $12.99")
    merchant: str = Field(description="Store, pharmacy, marketplace, or price service name")
    fulfillment: str = Field(description="pickup, delivery, shipping, coupon, or unknown")
    source: str = Field(description="Website where this price was found")
    url: str | None = Field(default=None, description="Product or price page URL if available")
    notes: str | None = Field(default=None, description="Short caveat such as membership, coupon, or out of stock")


class _BrowserPriceComparison(BaseModel):
    prices: list[_BrowserPriceOption] = Field(default_factory=list)


async def _fetch_browser_price_options_async(product: OTCProduct, address_hint: str) -> list[OTCPriceOption]:
    from browser_use_sdk.v3 import AsyncBrowserUse

    client = AsyncBrowserUse()
    task = (
        "Find current publicly visible consumer prices for this over-the-counter medicine. "
        "Return only prices you can read on the page today. Include both online delivery/shipping and local pickup "
        "or coupon options when visible. Do not invent prices. If a site asks for account checkout, skip it. "
        "Prefer GoodRx, Walmart, CVS, Walgreens, Target, Amazon, and Cost Plus Drugs when available. "
        f"Medicine: @{{{product.active_ingredient} {product.strength}}}. "
        f"Common package/quantity: @{{{product.package_size}}}. "
        f"User area for local pickup prices: @{{{address_hint}}}. "
        f"Return at most {BROWSER_PRICE_LIMIT} distinct price options."
    )
    run_kwargs = {
        "output_schema": _BrowserPriceComparison,
        "model": BROWSER_PRICE_MODEL,
        "proxy_country_code": "us",
        "allowed_domains": [
            "*.goodrx.com",
            "*.walmart.com",
            "*.cvs.com",
            "*.walgreens.com",
            "*.target.com",
            "*.amazon.com",
            "*.costplusdrugs.com",
        ],
    }
    try:
        result = await client.run(task, **run_kwargs)
    except TypeError:
        run_kwargs.pop("allowed_domains", None)
        result = await client.run(task, **run_kwargs)
    return [
        OTCPriceOption(
            product_name=item.product_name,
            price_usd=item.price_usd,
            merchant=item.merchant,
            fulfillment=item.fulfillment,
            source=item.source,
            url=item.url,
            notes=item.notes,
        )
        for item in result.output.prices[:BROWSER_PRICE_LIMIT]
        if item.price_usd.strip().startswith("$")
    ]


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
    thread.join(timeout=int(os.getenv("PHARMACY_BROWSER_PRICE_TIMEOUT_SECONDS", "90")))
    if thread.is_alive() or error is not None:
        return []
    return result or []


def browseruse_price_options(product: OTCProduct, address_hint: str) -> list[OTCPriceOption]:
    if not os.getenv("BROWSER_USE_API_KEY"):
        return []
    try:
        import browser_use_sdk.v3  # noqa: F401
    except Exception:
        return []
    return _run_async_in_thread(_fetch_browser_price_options_async(product, address_hint))


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
