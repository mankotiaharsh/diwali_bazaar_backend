import base64
import hashlib
import hmac
import json

import pytest

from app.services import receipts


def _checkout(client, body):
    r = client.post("/api/thalis/checkout", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _signed(payload: dict, secret: str = "test_secret", ts: str = "1730000000000"):
    raw = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), ts.encode() + raw, hashlib.sha256).digest()).decode()
    return raw, {"x-webhook-timestamp": ts, "x-webhook-signature": sig, "content-type": "application/json"}


@pytest.fixture
def sent_emails(monkeypatch):
    """Capture receipt emails instead of calling Resend."""
    outbox = []

    async def fake_send_email(**kw):
        outbox.append(kw)
        return True

    monkeypatch.setattr(receipts, "send_email", fake_send_email)
    return outbox


def test_health_and_config(client):
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert any("RESEND_API_KEY" in w for w in health["config_warnings"])
    cfg = client.get("/api/config").json()
    assert cfg == {
        "amount": "51",
        "min_amount": 51,
        "max_amount": 10000,
        "preset_amounts": [51, 101, 251, 501, 1100],
        "currency": "INR",
        "cashfree_mode": "sandbox",
        "payments_enabled": True,
        "receipts_enabled": False,
        "item_prices": cfg["item_prices"],
    }
    assert cfg["item_prices"]["dry-fruits"] == 10
    assert cfg["item_prices"]["mithai:laddoo"] == 0 and cfg["item_prices"]["mithai:jalebi"] == 10


def test_checkout_defaults_to_minimum_shagun(client, fake_cashfree, thali_body):
    # thali_body: golden diya + moli (free), jalebi + kaju katli (Rs 10 each)
    data = _checkout(client, thali_body)
    assert data["order_id"].startswith("diwali_")
    assert (data["shagun"], data["items_total"], data["amount"]) == ("51", "20", "71")
    sent = fake_cashfree.created[0]
    assert sent["amount_paise"] == 7100
    assert sent["customer_phone"] == "9876543210"  # +91 and spaces stripped
    assert sent["customer_email"] == "priya@example.com"


def test_sender_can_give_more(client, fake_cashfree, thali_body):
    data = _checkout(client, {**thali_body, "amount": 501})
    assert data["amount"] == "521"
    assert fake_cashfree.created[0]["amount_paise"] == 52100


@pytest.mark.parametrize("amount", [1, 50, 10001, 0, -5])
def test_amount_outside_limits_is_rejected(client, fake_cashfree, thali_body, amount):
    r = client.post("/api/thalis/checkout", json={**thali_body, "amount": amount})
    assert r.status_code == 422
    assert not fake_cashfree.created


def test_amount_must_be_whole_rupees(client, thali_body):
    assert client.post("/api/thalis/checkout", json={**thali_body, "amount": 51.5}).status_code == 422


def test_rejects_bad_input(client, thali_body):
    assert client.post("/api/thalis/checkout", json={**thali_body, "phone": "12345"}).status_code == 422
    assert client.post("/api/thalis/checkout", json={**thali_body, "email": "not-an-email"}).status_code == 422
    no_email = {k: v for k, v in thali_body.items() if k != "email"}
    assert client.post("/api/thalis/checkout", json=no_email).status_code == 422
    no_items = {**thali_body, "thali": {**thali_body["thali"], "items": []}}
    assert client.post("/api/thalis/checkout", json=no_items).status_code == 422
    long_note = {**thali_body, "thali": {**thali_body["thali"], "note": "x" * 301}}
    assert client.post("/api/thalis/checkout", json=long_note).status_code == 422


def test_full_payment_flow_sends_one_receipt(client, fake_cashfree, thali_body, sent_emails):
    order_id = _checkout(client, {**thali_body, "amount": 101})["order_id"]

    # Not paid yet: verify says pending, no link, no email.
    assert client.post("/api/thalis/verify", json={"order_id": order_id}).json() == {
        "status": "pending", "thali_id": None, "share_path": None,
    }
    assert sent_emails == []

    fake_cashfree.order_states[order_id] = "PAID"
    fake_cashfree.order_amounts[order_id] = 121.0  # Rs 101 shagun + Rs 20 items
    paid = client.post("/api/thalis/verify", json={"order_id": order_id}).json()
    assert paid["status"] == "paid" and paid["share_path"] == f"/t/{paid['thali_id']}"

    # Verifying again and a late webhook are harmless: still one receipt.
    assert client.post("/api/thalis/verify", json={"order_id": order_id}).json() == paid
    raw, headers = _signed({"data": {"order": {"order_id": order_id}}})
    client.post("/api/webhooks/cashfree", content=raw, headers=headers)
    assert len(sent_emails) == 1

    mail = sent_emails[0]
    assert mail["to"] == "priya@example.com"
    assert "121.00" in mail["html"] and f"/t/{paid['thali_id']}" in mail["html"]
    assert "Jalebi" in mail["html"] and "Golden Deepak" in mail["html"] and "Free" in mail["html"]
    name, pdf = mail["attachments"][0]
    assert name.startswith("Invoice-DB-") and pdf.startswith(b"%PDF")

    thali = client.get(f"/api/thalis/{paid['thali_id']}").json()
    assert thali["thali"]["from"] == "Priya"
    assert thali["thali"]["styles"]["mithai"] == ["jalebi", "kaju-katli"]
    exposed = json.dumps(thali)
    assert "9876543210" not in exposed and "example.com" not in exposed  # contact details never exposed


def test_receipt_escapes_user_text(client, fake_cashfree, thali_body, sent_emails):
    body = {**thali_body, "thali": {**thali_body["thali"], "from": "<b>Priya</b>", "to": "माँ 🪔"}}
    order_id = _checkout(client, body)["order_id"]
    fake_cashfree.order_states[order_id] = "PAID"
    client.post("/api/thalis/verify", json={"order_id": order_id})
    mail = sent_emails[0]
    assert "<b>Priya</b>" not in mail["html"] and "&lt;b&gt;Priya&lt;/b&gt;" in mail["html"]
    assert mail["attachments"][0][1].startswith(b"%PDF")  # non-Latin names don't break the PDF


def test_unpaid_thali_is_hidden(client, fake_cashfree, thali_body, sent_emails):
    order_id = _checkout(client, thali_body)["order_id"]
    fake_cashfree.order_states[order_id] = "EXPIRED"
    assert client.post("/api/thalis/verify", json={"order_id": order_id}).json()["status"] == "failed"
    assert client.get("/api/thalis/doesNotExist").status_code == 404
    assert sent_emails == []


def test_amount_mismatch_is_not_accepted(client, fake_cashfree, thali_body):
    order_id = _checkout(client, thali_body)["order_id"]
    fake_cashfree.order_states[order_id] = "PAID"
    fake_cashfree.order_amounts[order_id] = 1.0
    assert client.post("/api/thalis/verify", json={"order_id": order_id}).json()["status"] == "failed"


def test_unknown_order(client):
    assert client.post("/api/thalis/verify", json={"order_id": "diwali_0123456789abcdef"}).status_code == 404


def test_webhook_marks_thali_paid(client, fake_cashfree, thali_body, sent_emails):
    order_id = _checkout(client, thali_body)["order_id"]
    fake_cashfree.order_states[order_id] = "PAID"
    raw, headers = _signed({"type": "PAYMENT_SUCCESS_WEBHOOK", "data": {"order": {"order_id": order_id}}})
    assert client.post("/api/webhooks/cashfree", content=raw, headers=headers).json() == {"status": "ok"}
    assert client.post("/api/thalis/verify", json={"order_id": order_id}).json()["status"] == "paid"
    assert len(sent_emails) == 1


def test_webhook_rejects_bad_signature(client):
    raw, headers = _signed({"data": {"order": {"order_id": "diwali_x"}}}, secret="wrong")
    assert client.post("/api/webhooks/cashfree", content=raw, headers=headers).status_code == 401


def test_webhook_ignores_other_projects_orders(client):
    raw, headers = _signed({"data": {"order": {"order_id": "don_user_123"}}})
    assert client.post("/api/webhooks/cashfree", content=raw, headers=headers).json() == {"status": "ignored"}


def test_production_config_warnings():
    from app.config import Settings

    s = Settings(
        _env_file=None, cashfree_env="production", cashfree_app_id="x", cashfree_secret_key="y",
        frontend_base_url="http://localhost:5173", database_url="sqlite+aiosqlite:///x.db",
    )
    text = " ".join(s.config_warnings())
    assert "https return URLs" in text and "SQLite" in text and "webhooks are off" in text

    ok = Settings(
        _env_file=None, app_env="production", cashfree_env="production", cashfree_app_id="x",
        cashfree_secret_key="y", resend_api_key="k",
        frontend_base_url="https://diwali-bazaar.vercel.app", cors_origins="https://diwali-bazaar.vercel.app",
        backend_public_url="https://api.example.com", database_url="postgresql://u:p@h/db",
    )
    assert ok.config_warnings() == []


def test_free_only_thali_costs_just_the_shagun(client, fake_cashfree, thali_body):
    body = {**thali_body, "thali": {**thali_body["thali"], "items": ["diya", "pooja", "phool"],
            "styles": {"diya": ["mitti"], "pooja": ["moli", "kalash"], "phool": ["genda", "gulab"]}}}
    data = _checkout(client, body)
    assert (data["items_total"], data["amount"]) == ("0", "51")


def test_bill_uses_server_prices():
    from app.pricing import bill_lines

    content = {"items": ["dry-fruits", "sajavat", "chocolate"],
               "styles": {"sajavat": ["kandil", "toran", "lights"], "chocolate": ["milk", "box"], "phool": ["kamal"]}}
    assert [(line.name, line.price_paise) for line in bill_lines(content)] == [
        ("Dry Fruits", 1000), ("Akash Kandil", 0), ("Toran", 0), ("Fairy Lights", 1000),
        ("Milk Chocolate", 0), ("Gift Box", 1000),
    ]  # phool isn't on the thali, so its kamal isn't billed
