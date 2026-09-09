"""
Supplier Finder — sourcing side of the product (module #5).

Closes the COGS gap: discovery/Keepa give price & demand but never supplier cost.
This finds supplier offers for a product and produces a suggested landed COGS that
can flow straight into Product Hunter.

Built around a pluggable SupplierSource (same pattern as discovery): a labelled
sample source ships now; plug a real one (RapidAPI Alibaba/1688, or the official
Alibaba API) later via SUPPLIER_SOURCE + a key. All ranking and the COGS estimate
are deterministic Python — never an LLM.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

FREIGHT_PER_UNIT = 0.80   # rough sea-freight + duties per unit, $ (orientation value)


class SupplierSource(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[dict]:
        """Return supplier offers as dicts."""
        ...


@dataclass
class SupplierOffer:
    supplier: str
    country: str
    unit_price: float        # FOB price per unit, $
    moq: int                 # minimum order quantity
    lead_time_days: int
    rating: float            # 0-5
    est_landed_cost: float   # unit_price + freight/duties estimate
    url: str | None = None


# --- Sample source (orientation values, not live data) ----------------------

_TEMPLATES = [
    {"name": "Shenzhen {q} Mfg Co., Ltd", "country": "CN", "unit_price": 3.20, "moq": 500, "lead": 25, "rating": 4.7},
    {"name": "Guangzhou {q} Industrial", "country": "CN", "unit_price": 4.10, "moq": 300, "lead": 20, "rating": 4.8},
    {"name": "Yiwu {q} Trading Co.", "country": "CN", "unit_price": 2.80, "moq": 1000, "lead": 30, "rating": 4.4},
    {"name": "Hanoi {q} Co.", "country": "VN", "unit_price": 3.90, "moq": 250, "lead": 28, "rating": 4.5},
    {"name": "Delhi {q} Exports", "country": "IN", "unit_price": 3.60, "moq": 200, "lead": 35, "rating": 4.2},
]


class SampleSupplierSource:
    name = "sample"

    def search(self, query: str, limit: int) -> list[dict]:
        q = (query or "product").strip().title()
        offers = []
        for t in _TEMPLATES[:limit]:
            offers.append({
                "supplier": t["name"].format(q=q),
                "country": t["country"],
                "unit_price": t["unit_price"],
                "moq": t["moq"],
                "lead_time_days": t["lead"],
                "rating": t["rating"],
            })
        return offers


# --- RapidAPI (real Alibaba / 1688 supplier data) ---------------------------

def _to_float(v) -> float:
    """Parse a price that may be a number or a string like '$3.20' / '3.2-5.0'."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        import re
        m = re.search(r"\d+(?:[.,]\d+)?", v.replace(",", "."))
        if m:
            return float(m.group())
    return 0.0


def _pick(d: dict, keys: list[str], default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _rapidapi_items(data) -> list[dict]:
    """Find the product list inside varied RapidAPI response shapes."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "products", "data", "result", "results", "list", "offers"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):  # nested one level, e.g. {"data": {"items": [...]}}
                got = _rapidapi_items(v)
                if got:
                    return got
    return []


def _rapidapi_map(it: dict) -> dict | None:
    """Map one RapidAPI item to a supplier-offer dict. Field names guessed; finalize per API."""
    supplier = _pick(it, ["companyName", "supplierName", "sellerName", "shopName", "company", "seller", "title"])
    price = _to_float(_pick(it, ["price", "unitPrice", "salePrice", "minPrice", "wholesalePrice", "priceMin"], 0))
    if not supplier or price <= 0:
        return None
    return {
        "supplier": str(supplier)[:120],
        "country": _pick(it, ["country", "countryCode"], "CN"),
        "unit_price": round(price, 2),
        "moq": int(_to_float(_pick(it, ["moq", "minOrderQuantity", "minOrder", "minQuantity"], 0))) or 100,
        "lead_time_days": int(_to_float(_pick(it, ["leadTime", "deliveryTime", "shippingTime"], 0))) or 25,
        "rating": _to_float(_pick(it, ["rating", "score", "supplierRating", "starRating"], 4.5)),
        "url": _pick(it, ["url", "productUrl", "detailUrl", "link"]),
    }


class RapidApiSupplierSource:
    name = "rapidapi"

    def search(self, query: str, limit: int) -> list[dict]:
        import httpx

        key = os.environ.get("RAPIDAPI_KEY")
        host = os.environ.get("RAPIDAPI_HOST")
        if not key or not host:
            raise RuntimeError("RAPIDAPI_KEY and RAPIDAPI_HOST must be set in .env")
        path = os.getenv("RAPIDAPI_SEARCH_PATH", "/search")
        qparam = os.getenv("RAPIDAPI_QUERY_PARAM", "keyword")
        resp = httpx.get(
            f"https://{host}{path}",
            params={qparam: query},
            headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": host},
            timeout=60,
        )
        resp.raise_for_status()
        mapped = [_rapidapi_map(it) for it in _rapidapi_items(resp.json())[:limit]]
        return [m for m in mapped if m]


_SOURCES: dict[str, SupplierSource] = {
    "sample": SampleSupplierSource(),
    "rapidapi": RapidApiSupplierSource(),
}


def get_source() -> SupplierSource:
    return _SOURCES.get(os.getenv("SUPPLIER_SOURCE", "sample").lower(), _SOURCES["sample"])


def find_suppliers(
    query: str, limit: int = 10, *, max_moq: int | None = None, max_lead_time: int | None = None
) -> tuple[str, list[SupplierOffer], float | None]:
    """Find supplier offers, rank cheapest-first, and suggest a landed COGS."""
    source = get_source()
    raw = source.search(query, limit)

    offers: list[SupplierOffer] = []
    for o in raw:
        if max_moq is not None and o.get("moq", 0) > max_moq:
            continue
        if max_lead_time is not None and o.get("lead_time_days", 0) > max_lead_time:
            continue
        unit = float(o.get("unit_price", 0.0))
        offers.append(SupplierOffer(
            supplier=o.get("supplier", "Unknown"),
            country=o.get("country", "?"),
            unit_price=round(unit, 2),
            moq=int(o.get("moq", 0)),
            lead_time_days=int(o.get("lead_time_days", 0)),
            rating=float(o.get("rating", 0.0)),
            est_landed_cost=round(unit + FREIGHT_PER_UNIT, 2),
            url=o.get("url"),
        ))

    # Rank: cheapest landed cost first, then higher rating, then shorter lead time.
    offers.sort(key=lambda o: (o.est_landed_cost, -o.rating, o.lead_time_days))
    suggested_cogs = offers[0].est_landed_cost if offers else None
    return source.name, offers, suggested_cogs
