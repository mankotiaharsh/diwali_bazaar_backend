"""Sends email through Resend's HTTP API. Without RESEND_API_KEY, emails are only logged."""

import base64
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


async def send_email(
    *, to: str, subject: str, html: str, text: str, attachments: list[tuple[str, bytes]] | None = None
) -> bool:
    """Returns True if Resend accepted the email. Never raises — a failed email must not break payments."""
    settings = get_settings()
    if not settings.emails_enabled:
        logger.info("Email not sent (RESEND_API_KEY not set): %r to %s", subject, to)
        return False

    payload: dict = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    if settings.email_reply_to:
        payload["reply_to"] = settings.email_reply_to
    if attachments:
        payload["attachments"] = [
            {"filename": name, "content": base64.b64encode(data).decode()} for name, data in attachments
        ]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                RESEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}"},
            )
    except httpx.HTTPError as exc:
        logger.error("Could not reach Resend (%s) for %r to %s", exc.__class__.__name__, subject, to)
        return False

    if response.status_code >= 400:
        logger.error("Resend rejected %r to %s: %s %s", subject, to, response.status_code, response.text[:300])
        return False
    return True
