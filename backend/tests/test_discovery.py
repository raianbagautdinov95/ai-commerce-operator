"""Tests for product discovery: pluggable source feeds candidates into the engine."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force the offline sample source so tests never hit the real Keepa API.
os.environ["DISCOVERY_SOURCE"] = "sample"

from app.discovery import SampleDataSource, discover, _brand_domination, _cached_search, _CACHE
from app.decision_engine import Verdict


class _CountingSource:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def search(self, niche, limit):
        self.calls += 1
        return [{"name": "x", "price": 20, "cogs": 5, "fba_fee": 3, "monthly_sales": 100}]


def test_search_is_cached():
    _CACHE.clear()
    src = _CountingSource()
    raw1, cached1 = _cached_search(src, "widgets", 5)
    raw2, cached2 = _cached_search(src, "widgets", 5)
    assert src.calls == 1            # second call served from cache
    assert cached1 is False and cached2 is True
    assert raw1 == raw2
    # different niche -> a fresh call
    _cached_search(src, "gadgets", 5)
    assert src.calls == 2


def test_known_niche_returns_candidates():
    src = SampleDataSource()
    rows = src.search("dog products", limit=10)
    assert len(rows) > 0
    assert all("name" in r and "price" in r for r in rows)


def test_unknown_niche_falls_back_to_mixed():
    src = SampleDataSource()
    rows = src.search("zzz nonexistent niche", limit=10)
    assert len(rows) > 0  # mixed pool, never empty


def test_discover_ranks_best_first():
    source, evals = discover("kitchen", limit=10)
    assert source == "sample"
    assert len(evals) > 0
    scores = [e.score for e in evals if e.verdict != Verdict.AVOID]
    assert scores == sorted(scores, reverse=True)   # non-AVOID ranked high-to-low


def test_discover_respects_limit():
    _, evals = discover("fitness", limit=2)
    assert len(evals) <= 2


def test_brand_domination():
    # 10 listings: A x5 (50%), B x2 (20%), C/D/E x1 (10%) -> A and B hold >=20%
    products = [{"brand": "A"}] * 5 + [{"brand": "B"}] * 2 + [{"brand": b} for b in ("C", "D", "E")]
    assert _brand_domination(products) == 2
    # fragmented: 10 distinct brands, each 10% < threshold -> none dominant
    fragmented = [{"brand": f"B{i}"} for i in range(10)]
    assert _brand_domination(fragmented) == 0
    assert _brand_domination([]) == 0
    assert _brand_domination([{"brand": ""}, {"brand": None}]) == 0


def test_discover_filters():
    # kitchen sample prices include 27, 21, 18, 32 -> max_price 25 keeps only the cheaper ones
    _, evals = discover("kitchen", limit=10, max_price=25)
    assert all(e.economics.profit_per_unit is not None for e in evals)
    # every kept candidate's price must be <= 25 (check via no eval came from the >25 items)
    names = {e.name for e in evals}
    assert "Electric milk frother" not in names  # priced 32
    assert "Silicone baking molds" not in names  # priced 27

    _, evals2 = discover("kitchen", limit=10, min_monthly_sales=10_000)
    assert evals2 == []  # sample kitchen sales are in the hundreds


def test_keepa_status_disabled_for_sample():
    from app.discovery import keepa_status
    s = keepa_status()  # DISCOVERY_SOURCE is forced to "sample" in this test module
    assert s["enabled"] is False
    assert s["tokens_left"] is None


def test_patent_gate_flows_through_discovery():
    # the pet catalog includes a knockoff (patent_ok False) -> must be AVOID
    _, evals = discover("pet", limit=10)
    knockoff = next((e for e in evals if "knockoff" in e.name.lower()), None)
    assert knockoff is not None and knockoff.verdict == Verdict.AVOID
