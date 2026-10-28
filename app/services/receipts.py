"""Payment receipt email with a PDF invoice, sent once when a thali is paid."""

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import Thali, ThaliStatus, utcnow
from app.services.email import send_email

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
BRAND_RGB = (91, 33, 182)
METHOD_LABELS = {
    "upi": "UPI",
    "credit_card": "Credit card",
    "debit_card": "Debit card",
    "net_banking": "Net banking",
    "wallet": "Wallet",
    "pay_later": "Pay later",
    "cardless_emi": "Cardless EMI",
}


@dataclass(frozen=True)
class Receipt:
    invoice_no: str
    paid_on: str
    sender: str
    recipient: str
    email: str
    phone: str
    amount: str  # total paid, "121.00"
    shagun: str
    lines: tuple[tuple[str, str], ...]  # (item name, "10.00" or "Free")
    order_id: str
    payment_id: str
    method: str
    link: str
    support_email: str


def _money(paise: int) -> str:
    return f"{paise / 100:,.2f}"


def _price(paise: int) -> str:
    return "Free" if paise == 0 else _money(paise)


def _as_ist(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes; they are stored in UTC.
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(IST)


def build_receipt(thali: Thali, settings: Settings) -> Receipt:
    paid_at = _as_ist(thali.paid_at or thali.created_at)
    return Receipt(
        invoice_no=f"{settings.invoice_prefix}-{paid_at:%Y%m%d}-{thali.id:05d}",
        paid_on=paid_at.strftime("%d %b %Y, %I:%M %p IST"),
        sender=thali.sender_name,
        recipient=thali.recipient_name,
        email=thali.sender_email,
        phone=thali.sender_phone,
        amount=_money(thali.amount_paise),
        # Thalis paid before item pricing existed have no breakdown: the whole amount was shagun.
        shagun=_money(thali.amount_paise if thali.shagun_paise is None else thali.shagun_paise),
        lines=tuple((str(line["name"]), _price(int(line["price_paise"]))) for line in thali.line_items or []),
        order_id=thali.order_id,
        payment_id=thali.cf_payment_id or "—",
        method=METHOD_LABELS.get(thali.payment_method or "", (thali.payment_method or "Online").replace("_", " ").title()),
        link=f"{settings.frontend_base_url}/t/{thali.public_id}",
        support_email=settings.email_reply_to,
    )


# ── PDF invoice ────────────────────────────────────────────────────────────────


def _latin1(text: str) -> str:
    # The built-in PDF fonts only cover Latin-1; anything else (e.g. Hindi, emoji) becomes "?".
    return text.encode("latin-1", "replace").decode("latin-1")


def build_invoice_pdf(r: Receipt) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    w = pdf.epw

    def line(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(110, 90, 75)
        pdf.cell(42, 7, _latin1(label))
        pdf.set_text_color(40, 25, 15)
        pdf.cell(0, 7, _latin1(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Header band
    pdf.set_fill_color(*BRAND_RGB)
    pdf.rect(0, 0, 210, 34, style="F")
    pdf.set_xy(18, 10)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(w / 2, 10, "Diwali Bazaar")
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(w / 2, 10, "INVOICE", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(w, 6, "Digital Diwali thali greetings")

    pdf.set_xy(18, 44)
    line("Invoice no.", r.invoice_no)
    line("Date", r.paid_on)
    line("Order ID", r.order_id)
    line("Payment ID", r.payment_id)
    line("Payment method", r.method)

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*BRAND_RGB)
    pdf.cell(0, 8, "Billed to", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(40, 25, 15)
    for value in (r.sender, r.email, f"+91 {r.phone}"):
        pdf.cell(0, 6, _latin1(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Items table
    pdf.ln(6)
    pdf.set_fill_color(254, 243, 199)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(w - 60, 9, "  Description", fill=True)
    pdf.cell(20, 9, "Qty", align="C", fill=True)
    pdf.cell(40, 9, "Amount (INR)  ", align="R", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    for name, price in r.lines:
        pdf.cell(w - 60, 8, _latin1(f"  {name}"))
        pdf.cell(20, 8, "1", align="C")
        pdf.cell(40, 8, f"{price}  ", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(w - 60, 9, _latin1(f"  Shagun for {r.recipient}"))
    pdf.cell(20, 9, "1", align="C")
    pdf.cell(40, 9, f"{r.shagun}  ", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(230, 210, 180)
    pdf.line(18, pdf.get_y(), 18 + w, pdf.get_y())
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(w - 40, 11, "Total paid  ", align="R")
    pdf.cell(40, 11, f"INR {r.amount}  ", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(4)
    pdf.set_fill_color(220, 252, 231)
    pdf.set_text_color(22, 101, 52)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(28, 8, "PAID", align="C", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(8)
    pdf.set_text_color(110, 90, 75)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(w, 5, _latin1(f"Your thali link: {r.link}"))
    pdf.ln(2)
    notes = "This is a computer-generated invoice and does not need a signature. Payment processed by Cashfree Payments."
    if r.support_email:
        notes += f" Questions? Write to {r.support_email}."
    pdf.multi_cell(w, 5, _latin1(notes))
    return bytes(pdf.output())


# ── Email ──────────────────────────────────────────────────────────────────────


def build_email(r: Receipt) -> tuple[str, str, str]:
    e = {k: html.escape(v) for k, v in r.__dict__.items() if isinstance(v, str)}
    bill_rows = "".join(
        f'<tr><td style="padding:5px 0;color:#3b1d0f">{html.escape(name)}</td>'
        f'<td style="padding:5px 0;text-align:right;color:{"#16a34a" if price == "Free" else "#3b1d0f"}">'
        f'{"Free" if price == "Free" else "₹" + price}</td></tr>'
        for name, price in r.lines
    )
    bill_rows += (
        f'<tr><td style="padding:5px 0;color:#3b1d0f">Shagun for {e["recipient"]}</td>'
        f'<td style="padding:5px 0;text-align:right;color:#3b1d0f">₹{e["shagun"]}</td></tr>'
    )
    subject = f"Your Diwali thali for {r.recipient} is ready - receipt {r.invoice_no}"
    rows = "".join(
        f'<tr><td style="padding:6px 0;color:#8a5a3c">{label}</td>'
        f'<td style="padding:6px 0;text-align:right;color:#3b1d0f">{value}</td></tr>'
        for label, value in (
            ("Invoice no.", e["invoice_no"]),
            ("Date", e["paid_on"]),
            ("Order ID", e["order_id"]),
            ("Payment ID", e["payment_id"]),
            ("Payment method", e["method"]),
        )
    )
    support = (
        f'<p style="margin:16px 0 0;font-size:13px;color:#8a5a3c">Questions? Just reply to this email or write to '
        f'<a href="mailto:{e["support_email"]}" style="color:#5b21b6">{e["support_email"]}</a>.</p>'
        if r.support_email
        else ""
    )
    body = f"""<!doctype html>
<html><body style="margin:0;background:#fff7ed;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fff7ed;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden">
  <tr><td style="background:#5b21b6;padding:24px 28px;color:#ffffff">
    <div style="font-size:22px;font-weight:bold">🪔 Diwali Bazaar</div>
    <div style="font-size:14px;opacity:.85;margin-top:4px">Payment receipt</div>
  </td></tr>
  <tr><td style="padding:28px">
    <p style="margin:0 0 8px;font-size:18px;color:#3b1d0f">Thank you, {e["sender"]}!</p>
    <p style="margin:0 0 20px;font-size:15px;color:#3b1d0f;line-height:1.5">
      We received your payment of <b>₹{e["amount"]}</b>. Your Diwali thali for <b>{e["recipient"]}</b> is ready to share.
    </p>
    <p style="margin:0 0 24px;text-align:center">
      <a href="{e["link"]}" style="display:inline-block;background:#5b21b6;color:#ffffff;text-decoration:none;font-weight:bold;padding:12px 24px;border-radius:999px">Open your thali link</a>
    </p>
    <p style="margin:0 0 20px;font-size:13px;color:#8a5a3c;text-align:center;word-break:break-all">{e["link"]}</p>
    <p style="margin:0 0 6px;font-weight:bold;color:#5b21b6">Your thali</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;margin-bottom:16px">
      {bill_rows}
    </table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;border-top:1px solid #f3e8d5;border-bottom:1px solid #f3e8d5">
      {rows}
      <tr><td style="padding:10px 0;font-weight:bold;color:#3b1d0f">Total paid</td>
          <td style="padding:10px 0;text-align:right;font-weight:bold;font-size:16px;color:#5b21b6">₹{e["amount"]}</td></tr>
    </table>
    <p style="margin:16px 0 0;font-size:13px;color:#8a5a3c">Your invoice is attached as a PDF.</p>
    {support}
  </td></tr>
  <tr><td style="background:#fef3c7;padding:14px 28px;font-size:12px;color:#8a5a3c;text-align:center">
    Wishing you a bright and happy Diwali ✨
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
    text = (
        f"Thank you, {r.sender}!\n\n"
        f"We received your payment of Rs. {r.amount}. Your Diwali thali for {r.recipient} is ready:\n{r.link}\n\n"
        + "".join(f"{name}: {'Free' if price == 'Free' else 'Rs. ' + price}\n" for name, price in r.lines)
        + f"Shagun for {r.recipient}: Rs. {r.shagun}\n\n"
        + f"Invoice no.: {r.invoice_no}\nDate: {r.paid_on}\nOrder ID: {r.order_id}\n"
        f"Payment ID: {r.payment_id}\nPayment method: {r.method}\nTotal paid: Rs. {r.amount}\n\n"
        "Your invoice is attached as a PDF.\n"
        + (f"Questions? Write to {r.support_email}.\n" if r.support_email else "")
        + "\nWishing you a bright and happy Diwali!\nDiwali Bazaar"
    )
    return subject, body, text


async def send_receipt(thali_id: int) -> None:
    """Runs after the response is sent. Sends at most one receipt per thali; failures are logged, never raised."""
    try:
        async with SessionLocal() as session:
            thali = await session.get(Thali, thali_id)
            if thali is None or thali.status != ThaliStatus.PAID or thali.receipt_sent_at:
                return
            receipt = build_receipt(thali, get_settings())
            subject, body, text = build_email(receipt)
            sent = await send_email(
                to=thali.sender_email,
                subject=subject,
                html=body,
                text=text,
                attachments=[(f"Invoice-{receipt.invoice_no}.pdf", build_invoice_pdf(receipt))],
            )
            if sent:
                thali.receipt_sent_at = utcnow()
                await session.commit()
                logger.info("Receipt %s emailed for thali=%s", receipt.invoice_no, thali.public_id)
    except Exception:  # noqa: BLE001 — a receipt problem must never surface as a payment problem
        logger.exception("Sending receipt failed for thali id=%s", thali_id)
