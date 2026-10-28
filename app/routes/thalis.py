from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.deps import CashfreeDep, SessionDep, SettingsDep
from app.models import ThaliStatus
from app.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    PublicConfig,
    PublicId,
    PublicThali,
    ThaliContent,
    VerifyRequest,
    VerifyResponse,
)
from app.pricing import public_prices
from app.services import receipts, thalis

router = APIRouter(prefix="/api", tags=["Thalis"])


@router.get("/config", response_model=PublicConfig)
async def public_config(settings: SettingsDep) -> PublicConfig:
    """Price and Cashfree mode for the frontend (it loads the Cashfree SDK in the same mode)."""
    return PublicConfig(
        amount=settings.shagun_amount,
        min_amount=settings.min_amount,
        max_amount=settings.max_amount,
        preset_amounts=settings.preset_amounts,
        currency="INR",
        cashfree_mode=settings.cashfree_env,
        payments_enabled=settings.cashfree_enabled,
        receipts_enabled=settings.emails_enabled,
        item_prices=public_prices(),
    )


@router.post("/thalis/checkout", response_model=CheckoutResponse, status_code=201)
async def checkout(req: CheckoutRequest, session: SessionDep, cashfree: CashfreeDep, settings: SettingsDep):
    """Save the thali as pending and create a Cashfree order for the chosen shagun. Returns the checkout session id."""
    try:
        return await thalis.start_checkout(session, cashfree, settings, req)
    except thalis.InvalidAmount as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/thalis/verify", response_model=VerifyResponse)
async def verify(
    req: VerifyRequest, session: SessionDep, cashfree: CashfreeDep, background: BackgroundTasks
) -> VerifyResponse:
    """Call after the Cashfree popup closes (or on the return page). Confirms the payment with Cashfree itself."""
    thali, newly_paid = await thalis.sync_order(session, cashfree, req.order_id)
    if newly_paid:
        background.add_task(receipts.send_receipt, thali.id)
    if thali is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if thali.status == ThaliStatus.PAID:
        return VerifyResponse(status="paid", thali_id=thali.public_id, share_path=f"/t/{thali.public_id}")
    return VerifyResponse(status="failed" if thali.status == ThaliStatus.FAILED else "pending")


@router.get("/thalis/{public_id}", response_model=PublicThali)
async def get_thali(public_id: PublicId, session: SessionDep) -> PublicThali:
    """What the recipient's link loads. Unpaid or unknown ids look the same: not found."""
    thali = await thalis.get_paid_thali(session, public_id)
    if thali is None:
        raise HTTPException(status_code=404, detail="Thali not found")
    return PublicThali(id=thali.public_id, thali=ThaliContent.model_validate(thali.content))
