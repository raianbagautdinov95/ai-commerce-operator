"""
Product Discovery — the "find what sells" feed (module #4).

The decision engine already decides whether a product is worth selling; discovery
is the missing pipe that brings CANDIDATES in. It is built around a pluggable
DataSource so the data feed can be swapped without touching the ranking:

    DataSource.search(niche) -> candidate dicts -> decision_engine.rank() -> opportunities

DISCOVERY_SOURCE selects the source (default "sample"). The sample source returns a
curated, clearly-labelled catalog so the whole flow works with zero cost; plug in a
real source (Keepa / Rainforest / SP-API) later by adding a DataSource and a key.
Sample numbers are realistic orientation values, NOT live Amazon data.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from . import redaction
import time
from collections import Counter
from typing import Protocol

from dotenv import find_dotenv, load_dotenv

from . import decision_engine as de

load_dotenv(find_dotenv(usecwd=True))  # ensure KEEPA_API_KEY / DISCOVERY_* are available


class DataSource(Protocol):
    name: str

    def search(self, niche: str, limit: int) -> list[dict]:
        """Return candidate products as ProductInput-compatible dicts."""
        ...


# --- Sample catalog (orientation values, not live data) ---------------------

_CATALOG: dict[str, list[dict]] = {
    "kitchen": [
        {"name": "Silicone baking molds", "price": 27, "cogs": 6.5, "fba_fee": 3.30, "monthly_sales": 600,
         "ppc_per_unit": 2.5, "dominant_brands": 2, "median_reviews": 180},
        {"name": "Reusable produce bags", "price": 21, "cogs": 4, "fba_fee": 3.30, "monthly_sales": 650,
         "ppc_per_unit": 2.0, "dominant_brands": 2, "median_reviews": 140},
        {"name": "Stainless measuring cups", "price": 18, "cogs": 5.5, "fba_fee": 3.30, "monthly_sales": 500,
         "ppc_per_unit": 2.0, "dominant_brands": 4, "median_reviews": 600},
        {"name": "Electric milk frother", "price": 32, "cogs": 11, "fba_fee": 4.98, "monthly_sales": 700,
         "ppc_per_unit": 3.5, "dominant_brands": 5, "median_reviews": 2200, "cert_score": 0.5},
    ],
    "pet": [
        {"name": "Stainless dog bowl", "price": 24, "cogs": 4.5, "fba_fee": 3.30, "monthly_sales": 700,
         "ppc_per_unit": 2.0, "dominant_brands": 1, "median_reviews": 120},
        {"name": "Slow feeder dog bowl", "price": 19, "cogs": 4, "fba_fee": 3.30, "monthly_sales": 800,
         "ppc_per_unit": 2.5, "dominant_brands": 3, "median_reviews": 500},
        {"name": "Cat window perch", "price": 34, "cogs": 9, "fba_fee": 4.98, "monthly_sales": 450,
         "ppc_per_unit": 3.0, "size_score": 0.5, "dominant_brands": 3, "median_reviews": 700},
        {"name": "Branded chew toy (knockoff)", "price": 33, "cogs": 6, "fba_fee": 4.98, "monthly_sales": 800,
         "ppc_per_unit": 2.5, "dominant_brands": 2, "median_reviews": 100, "patent_ok": False},
    ],
    "fitness": [
        {"name": "Resistance bands set", "price": 26, "cogs": 5, "fba_fee": 3.30, "monthly_sales": 900,
         "ppc_per_unit": 3.0, "dominant_brands": 4, "median_reviews": 750},
        {"name": "Yoga mat (premium)", "price": 42, "cogs": 9, "fba_fee": 4.98, "monthly_sales": 900,
         "ppc_per_unit": 3.5, "size_score": 0.5, "dominant_brands": 3, "median_reviews": 400},
        {"name": "Cork yoga blocks", "price": 22, "cogs": 5.5, "fba_fee": 3.30, "monthly_sales": 550,
         "ppc_per_unit": 2.0, "dominant_brands": 2, "median_reviews": 160},
        {"name": "Adjustable dumbbell", "price": 89, "cogs": 38, "fba_fee": 8.74, "monthly_sales": 300,
         "ppc_per_unit": 5.0, "size_score": 0, "dominant_brands": 6, "median_reviews": 1800},
    ],
    "office": [
        {"name": "Bamboo desk organizer", "price": 29, "cogs": 7, "fba_fee": 4.98, "monthly_sales": 500,
         "ppc_per_unit": 2.5, "dominant_brands": 3, "median_reviews": 300},
        {"name": "Laptop stand (aluminium)", "price": 35, "cogs": 12, "fba_fee": 4.98, "monthly_sales": 650,
         "ppc_per_unit": 3.0, "dominant_brands": 5, "median_reviews": 1500},
        {"name": "Cable management box", "price": 20, "cogs": 4.5, "fba_fee": 3.30, "monthly_sales": 600,
         "ppc_per_unit": 2.0, "dominant_brands": 2, "median_reviews": 220},
    ],
    "baby": [
        {"name": "Silicone baby bibs", "price": 23, "cogs": 4.5, "fba_fee": 3.30, "monthly_sales": 700,
         "ppc_per_unit": 2.5, "dominant_brands": 3, "median_reviews": 350, "cert_score": 0.5},
        {"name": "Baby food maker", "price": 60, "cogs": 22, "fba_fee": 4.98, "monthly_sales": 350,
         "ppc_per_unit": 4.0, "dominant_brands": 5, "median_reviews": 1200, "cert_score": 0},
        {"name": "Wooden stacking toy", "price": 25, "cogs": 6, "fba_fee": 3.30, "monthly_sales": 500,
         "ppc_per_unit": 2.5, "dominant_brands": 2, "median_reviews": 200, "cert_score": 0.5},
    ],
}

# niche keyword -> catalog key
_ALIASES = {
    "kitchen": "kitchen", "cooking": "kitchen", "bake": "kitchen", "baking": "kitchen",
    "pet": "pet", "dog": "pet", "cat": "pet", "animal": "pet",
    "fitness": "fitness", "gym": "fitness", "yoga": "fitness", "sport": "fitness", "workout": "fitness",
    "office": "office", "desk": "office", "work": "office",
    "baby": "baby", "kid": "baby", "child": "baby", "toddler": "baby",
}


class SampleDataSource:
    name = "sample"

    def search(self, niche: str, limit: int) -> list[dict]:
        key = self._match(niche)
        if key:
            pool = _CATALOG[key]
        else:
            # No keyword match -> a mixed pool across niches.
            pool = [c for items in _CATALOG.values() for c in items[:1]]
        return pool[:limit]

    @staticmethod
    def _match(niche: str) -> str | None:
        n = (niche or "").lower()
        for kw, key in _ALIASES.items():
            if kw in n:
                return key
        return None


# --- Keepa: real Amazon data ------------------------------------------------

# Keepa stats.current is indexed by CSV type: 0 = Amazon price, 1 = New price (cents).
_KEEPA_AMAZON, _KEEPA_NEW = 0, 1

_BRAND_SHARE = 0.20   # a brand holding >=20% of the top results counts as "dominant"


def _brand_domination(products: list[dict]) -> int:
    """Niche competition signal: how many brands each hold >=20% of the top results."""
    brands = [(p.get("brand") or "").strip().lower() for p in products]
    brands = [b for b in brands if b]
    if not brands:
        return 0
    n = len(brands)
    return sum(1 for c in Counter(brands).values() if c / n >= _BRAND_SHARE)


def _keepa_map(p: dict, cogs_ratio: float) -> dict | None:
    """Map one Keepa product object -> ProductInput-compatible dict (None if unusable)."""
    st = p.get("stats") or {}
    cur = st.get("current") or []

    def at(i: int) -> int:
        return cur[i] if i < len(cur) else -1

    # Price (cents): prefer buy box, then New, then Amazon. Skip if none.
    price_cents = next(
        (c for c in (st.get("buyBoxPrice"), at(_KEEPA_NEW), at(_KEEPA_AMAZON))
         if isinstance(c, (int, float)) and c > 0),
        None,
    )
    if not price_cents:
        return None
    price = round(price_cents / 100, 2)
    # Realism: skip products too expensive to private-label (e.g. branded electronics).
    if price > float(os.getenv("DISCOVERY_MAX_PRICE", "75")):
        return None

    fba_cents = (p.get("fbaFees") or {}).get("pickAndPackFee") or 0
    fba_fee = round(fba_cents / 100, 2) if fba_cents > 0 else round(price * 0.15, 2)

    referral = p.get("referralFeePercent")
    referral_rate = round((referral if referral else 15) / 100, 4)

    msold = p.get("monthlySold")
    monthly_sales = msold if isinstance(msold, (int, float)) and msold > 0 else 0
    # Realism: a new private-label seller won't capture the incumbent's full volume.
    # Project a conservative capturable share, not the market leader's sales.
    monthly_sales = min(monthly_sales, int(os.getenv("DISCOVERY_SALES_CAP", "1500")))

    rc = (p.get("reviews") or {}).get("reviewCount") or []
    median_reviews = int(rc[-1]) if rc else 0          # this listing's review count, as a competition proxy

    weight = p.get("packageWeight") or p.get("itemWeight") or 0   # grams
    if weight <= 0:
        size_score = 1.0
    elif weight <= 450:
        size_score = 1.0
    elif weight <= 2000:
        size_score = 0.5
    else:
        size_score = 0.0

    return {
        "name": (p.get("title") or p.get("asin") or "Unknown")[:120],
        "price": price,
        "cogs": round(max(price * cogs_ratio, 0.01), 2),   # ESTIMATE — Keepa has no supplier cost
        "fba_fee": fba_fee,
        "monthly_sales": monthly_sales,
        "referral_rate": referral_rate,
        # Realistic launch ad cost so margins aren't overstated (Amazon sellers spend on PPC).
        "ppc_per_unit": round(price * float(os.getenv("DISCOVERY_PPC_RATIO", "0.12")), 2),
        "median_reviews": median_reviews,
        "size_score": size_score,
    }


class DiscoveryRequestError(RuntimeError):
    """A discovery call failed, said why, and did not say the key."""


@contextmanager
def _no_key_in_the_message():
    """Stop a failed Keepa call from putting the API key in its own exception.

    `httpx` puts the request URL in the message of anything `raise_for_status`
    raises, and the key travels in that URL as a query parameter. The traceback
    then reaches the log, the error reporter and any bug report pasted from
    either, carrying a long-lived credential nobody meant to send.

    The original is scrubbed in place rather than merely hidden. `raise ... from
    None` sets `__suppress_context__`, which stops Python's own traceback
    printer from showing the original — but `__context__` still holds it, and an
    error reporter that walks the chain finds the key sitting in there. Rewriting
    the original's own arguments means there is nothing to find however it is
    reached.
    """
    try:
        yield
    except Exception as exc:
        exc.args = tuple(redaction.redact(arg) for arg in exc.args)
        raise DiscoveryRequestError(
            f"{type(exc).__name__}: {redaction.redacted_message(exc)}") from None


class KeepaDataSource:
    name = "keepa"

    def search(self, niche: str, limit: int) -> list[dict]:
        import httpx  # local import so the sample source has no httpx dependency

        key = os.environ.get("KEEPA_API_KEY")
        if not key:
            raise RuntimeError("KEEPA_API_KEY is not set in the environment / .env")
        domain = int(os.getenv("DISCOVERY_DOMAIN", "1"))            # 1 = amazon.com
        cogs_ratio = float(os.getenv("DISCOVERY_COGS_RATIO", "0.30"))
        with _no_key_in_the_message():
            resp = httpx.get(
                "https://api.keepa.com/search",
                params={"key": key, "domain": domain, "type": "product",
                        "term": niche, "stats": 1, "rating": 1},
                timeout=60,
            )
            resp.raise_for_status()
        products = (resp.json().get("products") or [])[:limit]
        dominant = _brand_domination(products)   # niche-level competition signal
        mapped = []
        for p in products:
            m = _keepa_map(p, cogs_ratio)
            if m:
                m["dominant_brands"] = dominant
                mapped.append(m)
        return mapped


_SOURCES: dict[str, DataSource] = {"sample": SampleDataSource(), "keepa": KeepaDataSource()}


def get_source() -> DataSource:
    return _SOURCES.get(os.getenv("DISCOVERY_SOURCE", "sample").lower(), _SOURCES["sample"])


def keepa_status() -> dict:
    """Keepa token balance (the /token endpoint is free). Disabled unless keepa is active."""
    if get_source().name != "keepa" or not os.environ.get("KEEPA_API_KEY"):
        return {"enabled": False, "tokens_left": None, "refill_rate": None}
    import httpx

    with _no_key_in_the_message():
        r = httpx.get("https://api.keepa.com/token",
                      params={"key": os.environ["KEEPA_API_KEY"]}, timeout=30)
        r.raise_for_status()
    d = r.json()
    return {"enabled": True, "tokens_left": d.get("tokensLeft"), "refill_rate": d.get("refillRate")}


# Short-lived cache so repeating a niche search doesn't re-spend API tokens.
_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def _cache_ttl() -> float:
    try:
        return float(os.getenv("DISCOVERY_CACHE_TTL", "21600"))  # 6h default; <=0 disables
    except ValueError:
        return 21600.0


def _cached_search(source: DataSource, niche: str, limit: int) -> tuple[list[dict], bool]:
    """Return (candidates, was_cached). Caches per source/niche/limit/domain for the TTL."""
    ttl = _cache_ttl()
    key = (source.name, (niche or "").strip().lower(), limit, os.getenv("DISCOVERY_DOMAIN", "1"))
    now = time.time()
    if ttl > 0:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1], True
    raw = source.search(niche, limit)
    if ttl > 0:
        _CACHE[key] = (now + ttl, raw)
    return raw, False


def _run(
    niche: str, limit: int, max_price: float | None, min_monthly_sales: int | None
) -> tuple[str, list[tuple[de.Evaluation, dict]], bool]:
    source = get_source()
    raw, cached = _cached_search(source, niche, limit)

    def keep(c: dict) -> bool:
        if max_price is not None and c.get("price", 0) > max_price:
            return False
        if min_monthly_sales is not None and c.get("monthly_sales", 0) < min_monthly_sales:
            return False
        return True

    kept = [c for c in raw if keep(c)]
    evals = de.rank([de.ProductInput(**c) for c in kept])
    by_name = {c["name"]: c for c in kept}
    return source.name, [(e, by_name.get(e.name, {})) for e in evals], cached


def discover(
    niche: str, limit: int = 10, *, max_price: float | None = None, min_monthly_sales: int | None = None
) -> tuple[str, list[de.Evaluation]]:
    """Fetch candidate products for a niche, optionally filter, and rank best-first."""
    source, pairs, _ = _run(niche, limit, max_price, min_monthly_sales)
    return source, [e for e, _ in pairs]


def discover_detailed(
    niche: str, limit: int = 10, *, max_price: float | None = None, min_monthly_sales: int | None = None
) -> tuple[str, list[tuple[de.Evaluation, dict]], bool]:
    """Like discover(), but pairs each evaluation with its raw input dict and a cached flag."""
    return _run(niche, limit, max_price, min_monthly_sales)
