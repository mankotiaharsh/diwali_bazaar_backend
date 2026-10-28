"""Business logic: bill a thali (item prices + shagun), take payment with Cashfree, and confirm it."""

import logging
import secrets
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Thali, ThaliStatus, utcnow
from app.pricing import bill_lines
from app.schemas import CheckoutRequest, CheckoutResponse
from app.services.cashfree import CashfreeClient, CashfreeError

logger = logging.getLogger(__name__)

# Cashfree order states that will never turn into a payment.
_DEAD_ORDER_STATES = {"EXPIRED", "TERMINATED"}


async def _new_public_id(session: AsyncSession) -> str:
    for _ in range(5):
        candidate = secrets.token_urlsafe(6)  # 8 URL-safe characters
        if not await session.scalar(select(Thali.id).where(Thali.public_id == candidate)):
            return candidate
    raise RuntimeError("Could not generate a unique thali id")


class InvalidAmount(ValueError):
    pass


def resolve_amount_paise(settings: Settings, amount: int | None) -> int:
    """The sender may give more than the minimum shagun, within the server's limits."""
    rupees = settings.min_amount if amount is None else amount
    if not settings.min_amount <= rupees <= settings.max_amount:
        raise InvalidAmount(f"Shagun must be between ₹{settings.min_amount:,} and ₹{settings.max_amount:,}")
    return rupees * 100


async def start_checkout(
    session: AsyncSession, cashfree: CashfreeClient, settings: Settings, req: CheckoutRequest
) -> CheckoutResponse:
    shagun_paise = resolve_amount_paise(settings, req.amount)
    order_id = f"{settings.order_prefix}_{uuid4().hex[:24]}"
    public_id = await _new_public_id(session)
    content = req.thali.model_dump(by_alias=True)
    lines = bill_lines(content)
    items_paise = sum(line.price_paise for line in lines)
    amount_paise = items_paise + shagun_paise

    order = await cashfree.create_order(
        order_id=order_id,
        amount_paise=amount_paise,
        customer_id=f"{settings.order_prefix}_{req.phone}",
        customer_name=req.thali.sender,
        customer_phone=req.phone,
        customer_email=req.email,
        note=f"Diwali thali from {req.thali.sender} to {req.thali.to}",
        tags={"app": "diwali-bazaar", "thali": public_id},
        return_url=settings.payment_return_url,
        notify_url=settings.payment_notify_url,
        expires_at=utcnow() + timedelta(minutes=settings.order_expiry_minutes),
    )

    # Saved only after Cashfree accepted the order. Until the payment is confirmed the link stays hidden.
    session.add(
        Thali(
            public_id=public_id,
            order_id=order_id,
            status=ThaliStatus.PENDING,
            sender_name=req.thali.sender,
            recipient_name=req.thali.to,
            sender_phone=req.phone,
            sender_email=req.email,
            content=content,
            amount_paise=amount_paise,
            shagun_paise=shagun_paise,
            line_items=[line.as_dict() for line in lines],
            currency="INR",
            cf_order_id=str(order.get("cf_order_id") or "") or None,
        )
    )
    await session.commit()
    logger.info("Checkout started order=%s thali=%s amount=%s", order_id, public_id, amount_paise / 100)

    return CheckoutResponse(
        order_id=order_id,
        payment_session_id=order["payment_session_id"],
        cashfree_mode=settings.cashfree_env,
        amount=Decimal(amount_paise) / 100,
        items_total=Decimal(items_paise) / 100,
        shagun=Decimal(shagun_paise) / 100,
        currency="INR",
    )


async def sync_order(session: AsyncSession, cashfree: CashfreeClient, order_id: str) -> tuple[Thali | None, bool]:
    """
    Ask Cashfree for the real order status and update the thali to match.
    Safe to call any number of times (from the browser, the return page and the webhook).
    Returns (thali, newly_paid). newly_paid is True for exactly one caller: the one that should send the receipt.
    """
    thali = await session.scalar(select(Thali).where(Thali.order_id == order_id).with_for_update())
    if thali is None or thali.status == ThaliStatus.PAID:
        return thali, False

    newly_paid = False

    order = await cashfree.get_order(order_id)
    state = order.get("order_status")

    if state == "PAID":
        paid_paise = int(round(Decimal(str(order.get("order_amount", 0))) * 100))
        if paid_paise != thali.amount_paise or order.get("order_currency") != thali.currency:
            logger.error(
                "Amount mismatch on order=%s: expected %s %s, Cashfree says %s %s",
                order_id, thali.amount_paise, thali.currency, paid_paise, order.get("order_currency"),
            )
            thali.status = ThaliStatus.FAILED
        else:
            # Conditional update: if the webhook and the browser confirm at the same moment, only one wins.
            paid_at = utcnow()
            result = await session.execute(
                update(Thali)
                .where(Thali.id == thali.id, Thali.status != ThaliStatus.PAID)
                .values(status=ThaliStatus.PAID, paid_at=paid_at)
            )
            newly_paid = result.rowcount == 1
            thali.status, thali.paid_at = ThaliStatus.PAID, paid_at
            if newly_paid:
                await _attach_payment_details(cashfree, thali)
                logger.info("Payment confirmed order=%s thali=%s", order_id, thali.public_id)
    elif state in _DEAD_ORDER_STATES:
        thali.status = ThaliStatus.FAILED

    await session.commit()
    return thali, newly_paid


async def _attach_payment_details(cashfree: CashfreeClient, thali: Thali) -> None:
    """Best effort: record which payment and method paid the order. Never blocks the thali going live."""
    try:
        payments = await cashfree.get_payments(thali.order_id)
    except CashfreeError as exc:
        logger.warning("Could not fetch payments for order=%s: %s", thali.order_id, exc)
        return
    success = next((p for p in payments if p.get("payment_status") == "SUCCESS"), None)
    if success:
        thali.cf_payment_id = str(success.get("cf_payment_id") or "") or None
        thali.payment_method = (success.get("payment_group") or None) and str(success["payment_group"])[:32]


async def get_paid_thali(session: AsyncSession, public_id: str) -> Thali | None:
    return await session.scalar(
        select(Thali).where(Thali.public_id == public_id, Thali.status == ThaliStatus.PAID)
    )
