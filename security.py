from __future__ import annotations

import hashlib
import hmac
import logging

from config import SETTINGS

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify Meta webhook signature."""
    if not SETTINGS.app_secret:
        logger.warning("APP_SECRET missing — skipping webhook verification")
        return True

    if not signature:
        logger.warning("Missing X-Hub-Signature-256 header")
        return False

    try:
        expected_signature = "sha256=" + hmac.new(
            SETTINGS.app_secret.encode("utf-8"),
            msg=payload,
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_signature, signature)
    except Exception:
        logger.exception("Signature verification failed")
        return False


def verify_meta_verify_token(token: str) -> bool:
    """Constant-time comparison for the Meta webhook GET handshake token."""
    if not token or not SETTINGS.verify_token:
        return False
    return hmac.compare_digest(token, SETTINGS.verify_token)


def verify_telegram_secret(header_value: str) -> bool:
    """
    Verify Telegram's X-Telegram-Bot-Api-Secret-Token header.

    Without this check, anyone who knows (or guesses) an admin chat_id can POST
    a forged update straight to /telegram-webhook and issue admin commands.
    """
    if not SETTINGS.telegram_webhook_secret:
        return True
    if not header_value:
        return False
    return hmac.compare_digest(header_value, SETTINGS.telegram_webhook_secret)
