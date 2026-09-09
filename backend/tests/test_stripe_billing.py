import hashlib
import hmac

import pytest

from app import stripe_billing


def _configure(monkeypatch):
    values = {"STRIPE_SECRET_KEY": "sk_test_safe", "STRIPE_WEBHOOK_SECRET": "whsec_safe",
              "STRIPE_PRICE_OPERATOR": "price_operator", "STRIPE_SUCCESS_URL": "https://app.test/success",
              "STRIPE_CANCEL_URL": "https://app.test/cancel", "STRIPE_PORTAL_RETURN_URL": "https://app.test/setup"}
    for key, value in values.items(): monkeypatch.setenv(key, value)


def test_webhook_signature_and_timestamp_are_verified(monkeypatch):
    _configure(monkeypatch); body = b'{"id":"evt_1"}'; timestamp = 1_700_000_000
    digest = hmac.new(b"whsec_safe", str(timestamp).encode() + b"." + body,
                      hashlib.sha256).hexdigest()
    stripe_billing.verify_event(body, f"t={timestamp},v1={digest}", now=timestamp + 30)
    with pytest.raises(ValueError):
        stripe_billing.verify_event(body + b"x", f"t={timestamp},v1={digest}", now=timestamp)
    with pytest.raises(ValueError):
        stripe_billing.verify_event(body, f"t={timestamp},v1={digest}", now=timestamp + 301)


def test_checkout_is_server_priced_and_tenant_bound(monkeypatch):
    _configure(monkeypatch)
    class Response:
        status_code = 200
        def json(self): return {"url": "https://checkout.stripe.test/session"}
    class Client:
        def __init__(self): self.request = None
        def post(self, url, **kwargs): self.request = (url, kwargs); return Response()
    client = Client()
    url = stripe_billing.create_checkout(store_id="tenant-1", email="owner@example.test",
                                         plan="operator", client=client)
    assert url == "https://checkout.stripe.test/session"
    data = client.request[1]["data"]
    assert data["line_items[0][price]"] == "price_operator"
    assert data["metadata[store_id]"] == "tenant-1"
    assert "unit_amount" not in " ".join(data)
    with pytest.raises(ValueError):
        stripe_billing.create_checkout(store_id="tenant-1", email="x", plan="evil", client=client)
