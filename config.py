from __future__ import annotations

import os
import logging
import secrets
from dataclasses import dataclass
from typing import Tuple

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def get_env_var(key: str, default: str = "") -> str:
    """Read an environment variable, returning default if empty or missing."""
    return (os.getenv(key, default) or default).strip()


def require_env_var(key: str) -> str:
    """Read a required environment variable, raising if missing."""
    value = get_env_var(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def parse_csv_env_var(value: str) -> Tuple[str, ...]:
    """Parse a comma-separated env var into a deduplicated tuple."""
    if not value:
        return ()
    parts = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(dict.fromkeys(parts))


@dataclass(frozen=True)
class Settings:
    # Meta / Instagram Webhook
    verify_token: str
    app_secret: str
    page_id: str

    # Meta / Instagram Access Tokens
    own_account_id: str
    graph_access_token: str
    instagram_login_access_token: str

    # Database
    database_url: str

    # Telegram Admin Bot
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_admin_chat_ids: Tuple[str, ...]
    telegram_webhook_secret: str

    # URLs
    public_base_url: str

    # AI Services
    gemini_api_keys: Tuple[str, ...]
    groq_api_key: str
    groq_model: str

    # Application Config
    environment: str
    log_level: str
    port: int

    # Performance / Cache
    ai_cache_ttl_seconds: int
    ai_cache_maxsize: int
    db_pool_min: int
    db_pool_max: int


def load_settings() -> Settings:
    # Database URL: Render auto-injects DATABASE_URL; for local dev, use .env
    database_url = get_env_var("DATABASE_URL")
    if not database_url:
        database_url = require_env_var("DATABASE_URL")
    if "sslmode" not in database_url:
        database_url += ("&" if "?" in database_url else "?") + "sslmode=require"

    # Telegram admin IDs: fallback to main chat_id if not specified
    telegram_chat_id = require_env_var("TELEGRAM_CHAT_ID")
    admin_chat_ids = parse_csv_env_var(get_env_var("TELEGRAM_ADMIN_CHAT_IDS"))
    if not admin_chat_ids:
        admin_chat_ids = (telegram_chat_id,)
    elif telegram_chat_id not in admin_chat_ids:
        admin_chat_ids = tuple([telegram_chat_id, *admin_chat_ids])

    # Telegram webhook secret: generate if not provided
    webhook_secret = get_env_var("TELEGRAM_WEBHOOK_SECRET")
    if not webhook_secret:
        webhook_secret = secrets.token_urlsafe(32)
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET not set — generated a random one for this process. "
            "Set it explicitly in your environment so it stays stable across restarts."
        )

    # Public base URL: Render auto-injects RENDER_EXTERNAL_URL
    public_base_url = get_env_var("PUBLIC_BASE_URL", get_env_var("RENDER_EXTERNAL_URL"))

    # Gemini keys: comma-separated list
    gemini_api_keys = parse_csv_env_var(get_env_var("GEMINI_API_KEYS"))

    return Settings(
        # Meta / Instagram Webhook
        verify_token=require_env_var("VERIFY_TOKEN"),
        app_secret=require_env_var("APP_SECRET"),
        page_id=require_env_var("PAGE_ID"),

        # Meta / Instagram Access Tokens
        own_account_id=get_env_var("OWN_ACCOUNT_ID"),
        graph_access_token=get_env_var("GRAPH_ACCESS_TOKEN"),
        instagram_login_access_token=get_env_var("INSTAGRAM_LOGIN_ACCESS_TOKEN"),

        # Database
        database_url=database_url,

        # Telegram Admin Bot
        telegram_bot_token=require_env_var("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=telegram_chat_id,
        telegram_admin_chat_ids=admin_chat_ids,
        telegram_webhook_secret=webhook_secret,

        # URLs
        public_base_url=public_base_url,

        # AI Services
        gemini_api_keys=gemini_api_keys,
        groq_api_key=get_env_var("GROQ_API_KEY"),
        groq_model=get_env_var("GROQ_MODEL", "llama-3.3-70b-versatile"),

        # Application Config
        environment=get_env_var("APP_ENV", "production"),
        log_level=get_env_var("LOG_LEVEL", "INFO"),
        port=int(get_env_var("PORT", "10000")),

        # Performance / Cache
        ai_cache_ttl_seconds=int(get_env_var("AI_CACHE_TTL_SECONDS", "1800")),
        ai_cache_maxsize=int(get_env_var("AI_CACHE_MAXSIZE", "2000")),
        db_pool_min=int(get_env_var("DB_POOL_MIN", "2")),
        db_pool_max=int(get_env_var("DB_POOL_MAX", "10")),
    )


SETTINGS = load_settings()
