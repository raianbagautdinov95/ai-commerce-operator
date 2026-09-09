"""Tests for the supplier finder: ranking, COGS suggestion, filters."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["SUPPLIER_SOURCE"] = "sample"

from app.suppliers import find_suppliers, FREIGHT_PER_UNIT, _rapidapi_items, _rapidapi_map, _to_float


def test_returns_ranked_offers():
    source, offers, suggested = find_suppliers("silicone molds", limit=10)
    assert source == "sample"
    assert len(offers) > 0
    costs = [o.est_landed_cost for o in offers]
    assert costs == sorted(costs)                      # cheapest landed cost first
    assert "Silicone Molds" in offers[0].supplier      # query woven into supplier name


def test_suggested_cogs_is_cheapest_landed():
    _, offers, suggested = find_suppliers("dog bowl", limit=10)
    assert suggested == offers[0].est_landed_cost
    assert suggested == round(offers[0].unit_price + FREIGHT_PER_UNIT, 2)


def test_filters_moq_and_lead_time():
    _, offers, _ = find_suppliers("widget", limit=10, max_moq=300)
    assert all(o.moq <= 300 for o in offers)
    _, offers2, _ = find_suppliers("widget", limit=10, max_lead_time=22)
    assert all(o.lead_time_days <= 22 for o in offers2)


def test_empty_when_filters_exclude_all():
    _, offers, suggested = find_suppliers("widget", limit=10, max_moq=1)
    assert offers == []
    assert suggested is None


def test_rapidapi_price_parsing():
    assert _to_float(3.2) == 3.2
    assert _to_float("$3.20") == 3.2
    assert _to_float("3,5-5,0") == 3.5
    assert _to_float("n/a") == 0.0


def test_rapidapi_finds_list_in_varied_shapes():
    item = {"companyName": "ACME", "price": "$4.50"}
    assert _rapidapi_items([item]) == [item]
    assert _rapidapi_items({"data": {"items": [item]}}) == [item]
    assert _rapidapi_items({"products": [item]}) == [item]
    assert _rapidapi_items({"nope": 1}) == []


def test_rapidapi_map_offer():
    m = _rapidapi_map({"supplierName": "Yiwu Co", "price": "$3.20", "moq": "500", "rating": 4.8})
    assert m["supplier"] == "Yiwu Co"
    assert m["unit_price"] == 3.2
    assert m["moq"] == 500
    assert m["country"] == "CN"
    # unusable rows -> None
    assert _rapidapi_map({"price": 5}) is None          # no supplier name
    assert _rapidapi_map({"companyName": "X", "price": 0}) is None  # no price
