# Diwali Bazaar API (FastAPI)

Backend for the Diwali Bazaar thali app. A sender builds a thali in the React app, pays a **shagun (₹51 minimum, or any higher amount they choose)** through Cashfree, and only after the payment is confirmed does the share link (`/t/<id>`) start working. A receipt email with a PDF invoice is then sent once.

## Run locally

```powershell
cd C:\Users\Harsh\Desktop\diwali_backend
.\venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

- API docs (development only): http://localhost:8000/docs
- Health check: http://localhost:8000/health

Fresh machine? Create the environment first:

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env   # then fill in the Cashfree keys
```

Run the tests (they use a fake Cashfree, no network needed):

```powershell
pytest
```

## How the payment works

```
React app                       This API                          Cashfree
─────────                       ────────                          ────────
Pay ₹51 ──► POST /api/thalis/checkout ──► create ₹51 order ──────► order + payment_session_id
           (thali saved as "pending")
Cashfree popup opens with payment_session_id ──────────────────► user pays (UPI / card / …)
popup closes ──► POST /api/thalis/verify ──► GET order status ───► PAID?
           ◄── { status: "paid", share_path: "/t/AbC123xy" }      (thali becomes "paid")

                 POST /api/webhooks/cashfree ◄──────────────────── payment webhook (backup, if the tab was closed)

Recipient opens /t/AbC123xy ──► GET /api/thalis/AbC123xy ──► thali (only if paid)
```

Security choices:

- **Shagun limits live on the server** (`SHAGUN_AMOUNT` minimum, `SHAGUN_MAX_AMOUNT` maximum, whole rupees). Anything outside is rejected.
- **One receipt per payment:** the switch to "paid" is a conditional update, so the browser and the webhook can't both send a receipt.
- **Payment is confirmed with Cashfree's API**, never by trusting the browser. Webhooks are signature-checked and then also re-checked with the API.
- The paid amount and currency are compared with what was charged. A mismatch never unlocks a thali.
- **Unpaid and unknown thalis both return 404**, and the sender's phone number is never returned.
- Order ids start with `ORDER_PREFIX` (`diwali_`), so the shared Cashfree account's other orders are ignored by this webhook.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config` | Min/max/preset shagun, Cashfree mode, whether receipts are on |
| POST | `/api/thalis/checkout` | Body: `{ thali: { from, to, items, styles, note }, phone, email, amount? }` → `{ order_id, payment_session_id, cashfree_mode, amount }` |
| POST | `/api/thalis/verify` | Body: `{ order_id }` → `{ status: "paid" \| "pending" \| "failed", thali_id, share_path }` |
| GET | `/api/thalis/{id}` | The paid thali for the recipient's link |
| POST | `/api/webhooks/cashfree` | Cashfree payment webhooks |
| GET | `/health` | Database + Cashfree status |

## Settings (`.env`)

| Variable | Notes |
|---|---|
| `APP_ENV` | `development` shows `/docs`; `production` hides it |
| `DATABASE_URL` | SQLite locally. **Use Postgres in production** (Supabase, Neon…) — e.g. `postgresql://user:pass@host:5432/db`; the async driver is added automatically |
| `FRONTEND_BASE_URL` | The React app's URL; Cashfree returns users to `/payment/return?order_id=…` there |
| `CORS_ORIGINS` | Comma-separated frontend URLs allowed to call the API |
| `BACKEND_PUBLIC_URL` | This API's public **https** URL. When set, every order asks Cashfree to send webhooks to `/api/webhooks/cashfree` |
| `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY` | Dashboard → Developers → API Keys |
| `CASHFREE_ENV` | `sandbox` (test money) or `production` (real money) — the keys must match the mode |
| `SHAGUN_AMOUNT` / `SHAGUN_MAX_AMOUNT` | Minimum (default) and maximum shagun in whole rupees |
| `SHAGUN_PRESETS` | Quick-pick amounts shown in the app, e.g. `51,101,251,501,1100` |
| `RESEND_API_KEY` | Resend key for receipt emails; empty = emails are only logged |
| `EMAIL_FROM` | Sender, on a domain verified in Resend |
| `EMAIL_REPLY_TO` | Where customer replies go |
| `INVOICE_PREFIX` | Invoice numbers look like `DB-20261028-00003` |
| `ORDER_PREFIX`, `ORDER_EXPIRY_MINUTES` | Order id prefix and how long an unpaid order stays open |

## Deploying

1. Host the API on a service that runs Python (Render, Railway, a VPS…). Start command:
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
2. Use a **Postgres** `DATABASE_URL` — free-tier hosts wipe local files, so SQLite would lose paid thalis.
3. Set `APP_ENV=production`, `FRONTEND_BASE_URL` / `CORS_ORIGINS` to your Vercel URL, and `BACKEND_PUBLIC_URL` to the API's https URL.
4. Going live with real money: switch to the production keys and `CASHFREE_ENV=production`, and **whitelist your frontend domain** in the Cashfree dashboard.

## Project layout

```
app/
  main.py              app, CORS, error handling, health
  config.py            settings from .env
  database.py          async SQLAlchemy engine + session
  models.py            Thali table
  schemas.py           request/response validation
  deps.py              FastAPI dependencies
  routes/thalis.py     config, checkout, verify, get thali
  routes/webhooks.py   Cashfree webhook
  services/cashfree.py Cashfree API client + webhook signature check
  services/thalis.py   checkout + payment confirmation logic
  services/receipts.py receipt email + PDF invoice
  services/email.py    Resend client
tests/                 pytest suite with a fake Cashfree
```
