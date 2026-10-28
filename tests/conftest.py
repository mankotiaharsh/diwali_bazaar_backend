import os
import tempfile
from pathlib import Path

# Must be set before the app (and its settings / engine) is imported. Real env vars beat .env.
_tmp = Path(tempfile.mkdtemp())
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{(_tmp / 'test.db').as_posix()}",
    CASHFREE_APP_ID="test_app",
    CASHFREE_SECRET_KEY="test_secret",
    CASHFREE_ENV="sandbox",
    SHAGUN_AMOUNT="51",
    SHAGUN_MAX_AMOUNT="10000",
    SHAGUN_PRESETS="51,101,251,501,1100",
    RESEND_API_KEY="",
    EMAIL_REPLY_TO="help@example.com",
    ORDER_PREFIX="diwali",
    BACKEND_PUBLIC_URL="",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.deps import get_cashfree  # noqa: E402
from app.main import app  # noqa: E402
from app.services.cashfree import CashfreeClient  # noqa: E402


class FakeCashfree(CashfreeClient):
    """Stands in for Cashfree's API. Tests set `order_states[order_id]` to simulate payment."""

    def __init__(self):
        super().__init__(get_settings())
        self.created: list[dict] = []
        self.order_states: dict[str, str] = {}
        self.order_amounts: dict[str, float] = {}

    async def create_order(self, **kw):
        self.created.append(kw)
        self.order_states[kw["order_id"]] = "ACTIVE"
        return {"order_id": kw["order_id"], "cf_order_id": 12345, "payment_session_id": f"session_{kw['order_id']}"}

    async def get_order(self, order_id):
        return {
            "order_id": order_id,
            "order_status": self.order_states[order_id],
            "order_amount": self.order_amounts.get(order_id, 51.0),
            "order_currency": "INR",
        }

    async def get_payments(self, order_id):
        return [{"payment_status": "SUCCESS", "cf_payment_id": 999, "payment_group": "upi"}]


@pytest.fixture
def fake_cashfree():
    return FakeCashfree()


@pytest.fixture
def client(fake_cashfree):
    app.dependency_overrides[get_cashfree] = lambda: fake_cashfree
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def thali_body():
    return {
        "thali": {
            "from": "Priya",
            "to": "Maa",
            "items": ["diya", "mithai", "pooja"],
            "styles": {"diya": ["golden"], "mithai": ["jalebi", "kaju-katli"], "pooja": ["moli"]},
            "note": "Happy Diwali! 🪔",
        },
        "phone": "+91 98765 43210",
        "email": "Priya@Example.com",
    }
