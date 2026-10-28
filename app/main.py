import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal, engine, init_db
from app.routes import thalis, webhooks
from app.services.cashfree import CashfreeClient, CashfreeError

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("diwali")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.cashfree = CashfreeClient(settings)
    if settings.cashfree_enabled:
        logger.info("Cashfree ready (%s mode), shagun ₹%s", settings.cashfree_env, settings.shagun_amount)
    else:
        logger.warning("Cashfree credentials missing — checkout will return 503 until they are set")
    if not settings.payment_notify_url:
        logger.info("BACKEND_PUBLIC_URL is not https — Cashfree webhooks are off (fine for local development)")
    yield
    await app.state.cashfree.aclose()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
    # Interactive docs only while developing.
    docs_url="/docs" if settings.app_env == "development" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.app_env == "development" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(CashfreeError)
async def cashfree_error_handler(_: Request, exc: CashfreeError) -> JSONResponse:
    # Log the real reason; give the browser a safe, friendly message.
    logger.error("Cashfree error (status=%s): %s", exc.status_code, exc)
    if not exc.configured:
        return JSONResponse(status_code=503, content={"detail": "Payments are not set up yet."})
    return JSONResponse(
        status_code=502,
        content={"detail": "The payment service is unavailable right now. Please try again in a moment."},
    )


app.include_router(thalis.router)
app.include_router(webhooks.router)


@app.get("/", tags=["System"])
async def root() -> dict:
    return {"name": settings.app_name, "docs": "/docs" if settings.app_env == "development" else None}


@app.get("/health", tags=["System"])
async def health() -> JSONResponse:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db = "ok"
    except Exception:  # noqa: BLE001 — report any DB failure as unhealthy
        logger.exception("Health check: database unreachable")
        db = "unreachable"
    body = {
        "status": "ok" if db == "ok" else "degraded",
        "database": db,
        "cashfree": settings.cashfree_env if settings.cashfree_enabled else "not configured",
    }
    return JSONResponse(status_code=200 if db == "ok" else 503, content=body)
