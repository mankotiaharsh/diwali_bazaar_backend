from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ThaliStatus(StrEnum):
    PENDING = "pending"  # order created, waiting for payment
    PAID = "paid"  # payment confirmed with Cashfree — link is live
    FAILED = "failed"  # order expired / terminated / amount mismatch


class Thali(Base):
    """A thali and the Cashfree order that pays for it. The share link only works once status is PAID."""

    __tablename__ = "thalis"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=ThaliStatus.PENDING, index=True)

    sender_name: Mapped[str] = mapped_column(String(30))
    recipient_name: Mapped[str] = mapped_column(String(30))
    sender_phone: Mapped[str] = mapped_column(String(10))
    sender_email: Mapped[str] = mapped_column(String(254))
    # Everything the recipient sees: names, items, chosen designs and the note.
    content: Mapped[dict[str, Any]] = mapped_column(JSON)

    # Stored in paise to avoid floating-point money.
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    cf_order_id: Mapped[str | None] = mapped_column(String(64))
    cf_payment_id: Mapped[str | None] = mapped_column(String(64))
    payment_method: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
