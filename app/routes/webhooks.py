import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.deps import CashfreeDep, SessionDep, SettingsDep
from app.services import receipts, thalis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


@router.post("/cashfree")
async def cashfree_webhook(
    request: Request, session: SessionDep, cashfree: CashfreeDep, settings: SettingsDep, background: BackgroundTasks
):
    """
    Cashfree calls this when a payment succeeds or fails, so a thali goes live even if the
    buyer closed the tab right after paying. We only use it as a nudge: the order status is
    always re-read from Cashfree's API, never trusted from the webhook body.
    """
    raw_body = await request.body()
    if not cashfree.verify_webhook_signature(
        raw_body=raw_body,
        timestamp=request.headers.get("x-webhook-timestamp", ""),
        signature=request.headers.get("x-webhook-signature", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
        order_id = payload["data"]["order"]["order_id"]
    except (ValueError, KeyError, TypeError):
        logger.info("Webhook without an order id, ignoring (type=%s)", request.headers.get("x-webhook-type"))
        return {"status": "ignored"}

    # The Cashfree account may be shared with other projects — leave their orders alone.
    if not str(order_id).startswith(f"{settings.order_prefix}_"):
        return {"status": "ignored"}

    # A CashfreeError here becomes a non-2xx response, so Cashfree retries the webhook later.
    thali, newly_paid = await thalis.sync_order(session, cashfree, order_id)
    if newly_paid:
        background.add_task(receipts.send_receipt, thali.id)
    return {"status": "ok" if thali else "unknown_order"}
