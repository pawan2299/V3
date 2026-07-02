from __future__ import annotations

import logging
import threading
import atexit
import signal
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, render_template

from bot_logic import handle_comment, handle_new_follower
from config import SETTINGS
from database import init_database, get_stats, is_bot_paused, is_safe_mode, db_ping
from security import verify_webhook_signature, verify_meta_verify_token, verify_telegram_secret
from telegram_bot import get_webhook_info, handle_update, register_telegram_webhook
from instagram_api import check_token_validity

logging.basicConfig(
    level=getattr(logging, SETTINGS.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB
_executor = ThreadPoolExecutor(max_workers=4)
_init_lock = threading.Lock()
_init_done = False


def _startup_once() -> None:
    global _init_done
    with _init_lock:
        if _init_done:
            return
        logger.info("Starting KrishnaVerse AI initialization")
        init_database()
        if SETTINGS.telegram_bot_token and SETTINGS.public_base_url:
            register_telegram_webhook()
        try:
            check_token_validity("graph")
        except Exception:
            logger.exception("Graph token validation failed")
        try:
            check_token_validity("dm")
        except Exception:
            logger.exception("DM token validation failed")
        _init_done = True
        logger.info("Initialization complete")


@app.before_request
def ensure_startup():
    _startup_once()


@app.get("/")
def health():
    stats = get_stats()
    status_data = {
        "status": "KrishnaVerse AI is live",
        "environment": SETTINGS.environment,
        "database": "ok" if db_ping() else "unreachable",
        "paused": is_bot_paused(),
        "safe_mode": is_safe_mode(),
        "stats": stats,
    }
    return render_template("index.html", status=status_data)


@app.get("/webhook")
def verify_webhook():
    if (
        request.args.get("hub.mode") == "subscribe"
        and verify_meta_verify_token(request.args.get("hub.verify_token", ""))
    ):
        return request.args.get("hub.challenge", ""), 200
    return "Forbidden", 403


@app.post("/webhook")
def webhook():
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(request.data, signature):
        logger.warning("Invalid webhook signature")
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}

    def process_payload():
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                field = change.get("field")
                value = change.get("value", {}) or {}
                if field == "comments":
                    handle_comment(value)
                elif field == "follows":
                    handle_new_follower(value.get("id", ""), value.get("username", ""))

    _executor.submit(process_payload)
    return "OK", 200


@app.post("/telegram-webhook")
def telegram_webhook():
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not verify_telegram_secret(header):
        logger.warning("Rejected Telegram webhook call with invalid/missing secret token")
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    _executor.submit(handle_update, data)
    return "OK", 200


@app.get("/stats")
def stats():
    return jsonify(get_stats()), 200


@app.get("/telegram-webhook-info")
def telegram_webhook_info():
    return jsonify(get_webhook_info()), 200


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(Exception)
def handle_exception(exc):
    logger.error("Unhandled error: %s", exc, exc_info=True)
    return jsonify({"error": "Internal server error"}), 500


def _shutdown(*_args) -> None:
    logger.info("Shutting down — draining background task queue")
    _executor.shutdown(wait=True, cancel_futures=False)


atexit.register(_shutdown)
signal.signal(signal.SIGTERM, _shutdown)


if __name__ == "__main__":
    _startup_once()
    app.run(host="0.0.0.0", port=SETTINGS.port, debug=False)
