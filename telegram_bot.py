from __future__ import annotations

import html
import logging
import traceback
from datetime import datetime

import requests

from ai_router import clear_cache, get_status as get_ai_status
from config import SETTINGS
from database import (
    add_keyword,
    get_recent_activity,
    get_stats,
    is_bot_paused,
    is_gemini_enabled,
    is_safe_mode,
    list_keywords,
    remove_keyword,
    set_bot_paused,
    set_gemini_enabled,
    set_safe_mode,
    get_ai_usage_summary,
    db_ping,
)

logger = logging.getLogger(__name__)
_session = requests.Session()
AUTHORIZED_ADMINS = set(SETTINGS.telegram_admin_chat_ids)
API_BASE = f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}"

_user_states: dict[str, dict] = {}


def _escape(text: str) -> str:
    return html.escape(text or "")


def _build_inline_keyboard(buttons: list[list[tuple[str, str]]]) -> dict:
    keyboard = []
    for row in buttons:
        keyboard.append([{"text": text, "callback_data": data} for text, data in row])
    return {"inline_keyboard": keyboard}


def _send_message(chat_id: str, text: str, parse_mode: str = "HTML", reply_markup: dict | None = None, disable_notification: bool = False):
    if not SETTINGS.telegram_bot_token:
        return None
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "disable_notification": disable_notification,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = _session.post(f"{API_BASE}/sendMessage", json=payload, timeout=(5, 30))
        if not resp.ok:
            logger.error("Telegram send error %s: %s", resp.status_code, resp.text[:400])
            return None
        return resp.json()
    except Exception:
        logger.exception("Telegram request failed")
        return None


def _edit_message(chat_id: str, message_id: int, text: str, parse_mode: str = "HTML", reply_markup: dict | None = None):
    try:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = _session.post(f"{API_BASE}/editMessageText", json=payload, timeout=(5, 30))
        return resp.json() if resp.ok else None
    except Exception:
        logger.exception("Telegram edit failed")
        return None


def _answer_callback(callback_query_id: str, text: str | None = None):
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        _session.post(f"{API_BASE}/answerCallbackQuery", json=payload, timeout=(5, 10))
    except Exception:
        logger.exception("Telegram callback answer failed")


def _delete_message(chat_id: str, message_id: int):
    try:
        resp = _session.post(f"{API_BASE}/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}, timeout=(5, 10))
        return resp.ok
    except Exception:
        logger.exception("Telegram delete message failed")
        return False


def get_webhook_info() -> dict:
    if not SETTINGS.telegram_bot_token:
        return {}
    try:
        resp = _session.get(f"{API_BASE}/getWebhookInfo", timeout=(5, 20))
        return resp.json() if resp.ok else {"ok": False, "error": resp.text[:200]}
    except Exception:
        logger.exception("Telegram getWebhookInfo failed")
        return {"ok": False}


def register_telegram_webhook() -> bool:
    if not SETTINGS.telegram_bot_token or not SETTINGS.public_base_url:
        logger.info("Skipping Telegram webhook registration (missing token or public base url)")
        return False
    url = SETTINGS.public_base_url.rstrip("/") + "/telegram-webhook"
    try:
        resp = _session.post(
            f"{API_BASE}/setWebhook",
            json={
                "url": url,
                "secret_token": SETTINGS.telegram_webhook_secret,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=(10, 30),
        )
        if not resp.ok:
            logger.error("Telegram setWebhook error %s: %s", resp.status_code, resp.text[:400])
            return False
        data = resp.json()
        logger.info("Telegram webhook set: %s", data)
        return bool(data.get("ok"))
    except Exception:
        logger.exception("Telegram webhook registration failed")
        return False


def _is_admin(chat_id: str) -> bool:
    return chat_id in AUTHORIZED_ADMINS


def handle_update(update: dict):
    try:
        callback_query = update.get("callback_query")
        if callback_query:
            handle_callback_query(callback_query)
            return

        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not _is_admin(chat_id):
            logger.warning("Unauthorized Telegram chat: %s", chat_id)
            return
        if not text:
            return

        if chat_id in _user_states:
            handle_conversation_input(chat_id, text)
            return

        if text.startswith("/"):
            handle_command(chat_id, text)
        else:
            show_main_menu(chat_id)
    except Exception:
        logger.error("Telegram update handler failed:
%s", traceback.format_exc())


def handle_command(chat_id: str, text: str):
    logger.info("Received Telegram command: %s | chat_id=%s", text.split()[0], chat_id)
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    handlers = {
        "/start": lambda: show_main_menu(chat_id),
        "/help": lambda: show_main_menu(chat_id),
        "/menu": lambda: show_main_menu(chat_id),
        "/status": lambda: _send_message(chat_id, _build_status_text(), reply_markup=_build_inline_keyboard([[("🔄 Refresh", "refresh:status")]])),
        "/stats": lambda: _send_message(chat_id, _build_stats_text()),
        "/dashboard": lambda: show_dashboard(chat_id),
        "/pause": lambda: _set_pause(chat_id, True),
        "/resume": lambda: _set_pause(chat_id, False),
        "/ping": lambda: _send_message(chat_id, "🏓 Pong

✅ Bot is running."),
        "/ai": lambda: _send_message(chat_id, _build_ai_text()),
        "/models": lambda: _send_message(chat_id, _build_ai_text()),
        "/quota": lambda: _send_message(chat_id, _build_ai_text()),
        "/analytics": lambda: show_analytics(chat_id),
        "/keywords": lambda: show_keywords_menu(chat_id),
        "/addkeyword": lambda: start_add_keyword_wizard(chat_id),
        "/removekeyword": lambda: start_remove_keyword_wizard(chat_id),
        "/clearcache": lambda: _clear_cache(chat_id),
        "/safe_on": lambda: _set_safe_mode(chat_id, True),
        "/safe_off": lambda: _set_safe_mode(chat_id, False),
        "/gemini_on": lambda: _set_gemini(chat_id, True),
        "/gemini_off": lambda: _set_gemini(chat_id, False),
        "/activity": lambda: _send_message(chat_id, _build_activity_text()),
        "/logs": lambda: show_logs(chat_id),
        "/health": lambda: show_health_check(chat_id),
    }

    handler = handlers.get(cmd)
    if handler:
        try:
            handler()
        except Exception as exc:
            logger.error("Command handler failed for %s: %s", cmd, traceback.format_exc())
            _send_message(chat_id, f"❌ Error executing command <b>{cmd}</b>: {_escape(str(exc))}")
    else:
        _send_message(chat_id, "❓ Unknown command. Use /menu to open the control panel.")


def handle_callback_query(callback_query: dict):
    try:
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
        if not _is_admin(chat_id):
            return
        data = callback_query.get("data", "")
        message_id = callback_query["message"]["message_id"]

        handled = False

        if data == "refresh:status":
            _edit_message(chat_id, message_id, _build_status_text(), reply_markup=_build_inline_keyboard([[("🔄 Refresh", "refresh:status")]]))
            handled = True
        elif data == "menu:dashboard":
            show_dashboard(chat_id)
            handled = True
        elif data == "menu:analytics":
            show_analytics(chat_id)
            handled = True
        elif data == "menu:keywords":
            show_keywords_menu(chat_id)
            handled = True
        elif data == "menu:settings":
            show_settings_menu(chat_id)
            handled = True
        elif data == "menu:activity":
            show_activity_log(chat_id)
            handled = True
        elif data == "menu:health":
            show_health_check(chat_id)
            handled = True
        elif data == "back:main":
            show_main_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "back:keywords":
            show_keywords_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "back:settings":
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "toggle:pause":
            set_bot_paused(not is_bot_paused())
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "toggle:gemini":
            set_gemini_enabled(not is_gemini_enabled())
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "toggle:safe":
            set_safe_mode(not is_safe_mode())
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "action:clearcache":
            clear_cache()
            _answer_callback(callback_query.get("id", ""), "✅ Cache cleared")
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data.startswith("kw:del:"):
            keyword = data[7:]
            remove_keyword(keyword)
            show_keywords_menu(chat_id, edit=True, message_id=message_id)
            _answer_callback(callback_query.get("id", ""), f"✅ Removed {keyword}")
            handled = True
        elif data == "kw:add":
            start_add_keyword_wizard(chat_id)
            _delete_message(chat_id, message_id)
            handled = True
        elif data == "kw:remove":
            start_remove_keyword_wizard(chat_id)
            _delete_message(chat_id, message_id)
            handled = True
        elif data == "refresh:analytics":
            show_analytics(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "refresh:dashboard":
            show_dashboard(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "refresh:activity":
            show_activity_log(chat_id, edit=True, message_id=message_id)
            handled = True

        if handled:
            _answer_callback(callback_query.get("id", ""))
        else:
            _answer_callback(callback_query.get("id", ""), "⚠️ Action not implemented yet")

    except Exception:
        logger.exception("Callback query handler failed")


def handle_conversation_input(chat_id: str, text: str):
    state = _user_states.get(chat_id, {})
    action = state.get("action")

    if action == "add_keyword_step1":
        _user_states[chat_id]["keyword"] = text[:64]
        _user_states[chat_id]["action"] = "add_keyword_step2"
        _send_message(chat_id, f"📝 Now send the <b>reply text</b> for keyword <code>{_escape(text[:64])}</code>:

(Will be truncated to 500 chars)")
    elif action == "add_keyword_step2":
        keyword = state.get("keyword", "")
        reply = text[:500]
        add_keyword(keyword, reply)
        del _user_states[chat_id]
        _send_message(chat_id, f"✅ Keyword <code>{_escape(keyword)}</code> added successfully!", reply_markup=_build_inline_keyboard([[("🔙 Back to Keywords", "back:keywords")]]))
    elif action == "remove_keyword_step1":
        if remove_keyword(text):
            _send_message(chat_id, f"✅ Keyword <code>{_escape(text)}</code> removed!", reply_markup=_build_inline_keyboard([[("🔙 Back to Keywords", "back:keywords")]]))
        else:
            _send_message(chat_id, f"❌ Keyword <code>{_escape(text)}</code> not found.")
        del _user_states[chat_id]
    else:
        del _user_states[chat_id]


def _set_pause(chat_id: str, value: bool):
    set_bot_paused(value)
    _send_message(chat_id, f"✅ Bot {'paused' if value else 'resumed'}.")


def _set_safe_mode(chat_id: str, value: bool):
    set_safe_mode(value)
    _send_message(chat_id, f"✅ Safe mode {'enabled' if value else 'disabled'}.")


def _set_gemini(chat_id: str, value: bool):
    set_gemini_enabled(value)
    _send_message(chat_id, f"✅ Gemini {'enabled' if value else 'disabled'}.")


def _clear_cache(chat_id: str):
    clear_cache()
    _send_message(chat_id, "✅ AI cache cleared.")


def show_main_menu(chat_id: str, edit: bool = False, message_id: int | None = None):
    stats = get_stats()
    ai_status = get_ai_status()

    bot_status = "⏸️ <b>PAUSED</b>" if stats["bot_paused"] else "✅ <b>RUNNING</b>"
    gemini_status = "🟢 ON" if stats["gemini_enabled"] else "🔴 OFF"
    safe_status = "🛡️ ON" if stats["safe_mode"] else "⚪ OFF"

    text = (
        f"🦚 <b>KrishnaVerse AI Control Panel</b>

"
        f"├─ Status: {bot_status}
"
        f"├─ Gemini: {gemini_status}
"
        f"└─ Safe Mode: {safe_status}

"
        f"📊 <b>Quick Stats:</b>
"
        f"├─ Comments Replied: {stats['comments_replied']}
"
        f"├─ Welcome DMs: {stats['welcome_dms_sent']}
"
        f"└─ AI Calls (24h): {stats['ai_calls_24h']}

"
        f"<i>Select an option below:</i>"
    )

    keyboard = _build_inline_keyboard([
        [("📈 Dashboard", "menu:dashboard"), ("📊 Analytics", "menu:analytics")],
        [("🔑 Keywords", "menu:keywords"), ("⚙️ Settings", "menu:settings")],
        [("📝 Activity Log", "menu:activity"), ("💚 Health Check", "menu:health")],
    ])

    if edit and message_id:
        _edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send_message(chat_id, text, reply_markup=keyboard)


def show_dashboard(chat_id: str, edit: bool = False, message_id: int | None = None):
    stats = get_stats()
    ai_status = get_ai_status()

    gemini_data = ai_status.get("gemini", {})
    keys = gemini_data.get("keys", [])
    total_quota_hits = sum(k.get("quota_hits_today", 0) for k in keys)
    total_requests = sum(k.get("requests_today", 0) for k in keys)

    text = (
        f"📈 <b>Bot Dashboard</b>

"
        f"<b>🤖 Bot Status:</b>
"
        f"├─ State: {'⏸️ Paused' if stats['bot_paused'] else '✅ Running'}
"
        f"├─ Gemini: {'🟢 Enabled' if stats['gemini_enabled'] else '🔴 Disabled'}
"
        f"└─ Safe Mode: {'🛡️ Active' if stats['safe_mode'] else '⚪ Inactive'}

"
        f"<b>📊 Today's Performance:</b>
"
        f"├─ Comments Replied: <b>{stats['comments_replied']}</b>
"
        f"├─ Welcome DMs Sent: <b>{stats['welcome_dms_sent']}</b>
"
        f"├─ Events Processed: <b>{stats['processed_events']}</b>
"
        f"└─ AI API Calls: <b>{stats['ai_calls_24h']}</b>

"
        f"<b>🧠 AI Usage:</b>
"
        f"├─ Total Requests: <b>{total_requests}</b>
"
        f"├─ Quota Hits: <b>{total_quota_hits}</b>
"
        f"├─ Cache Size: <b>{ai_status.get('cache', {}).get('size', 0)}</b>
"
        f"└─ Groq Fallback: {'✅' if ai_status.get('groq_enabled') else '❌'}

"
        f"<b>🔑 Configured Keywords:</b> {stats['keywords']}"
    )

    keyboard = _build_inline_keyboard([
        [("🔄 Refresh", "refresh:dashboard"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send_message(chat_id, text, reply_markup=keyboard)


def show_analytics(chat_id: str, edit: bool = False, message_id: int | None = None):
    usage_summary = get_ai_usage_summary(days=1)

    if not usage_summary:
        text = "📊 <b>Analytics</b>

No AI usage data available yet."
    else:
        lines = ["📊 <b>AI Analytics (Last 24h)</b>
"]

        provider_stats = {}
        for row in usage_summary:
            provider = row["provider"]
            model = row["model"]
            status = row["status"]
            count = row["count"]
            latency = row["avg_latency_ms"]

            key = f"{provider}/{model}"
            if key not in provider_stats:
                provider_stats[key] = {"success": 0, "error": 0, "total_latency": 0}

            if status == "success":
                provider_stats[key]["success"] += count
            else:
                provider_stats[key]["error"] += count
            provider_stats[key]["total_latency"] += latency * count

        for key, data in provider_stats.items():
            total = data["success"] + data["error"]
            success_rate = (data["success"] / total * 100) if total > 0 else 0
            avg_latency = data["total_latency"] / total if total > 0 else 0

            lines.append(f"<b>{key}</b>:")
            lines.append(f"  ├─ Requests: {total}")
            lines.append(f"  ├─ Success Rate: {success_rate:.1f}%")
            lines.append(f"  └─ Avg Latency: {avg_latency:.0f}ms")
            lines.append("")

        text = "
".join(lines)

    keyboard = _build_inline_keyboard([
        [("🔄 Refresh", "refresh:analytics"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send_message(chat_id, text, reply_markup=keyboard)


def show_keywords_menu(chat_id: str, edit: bool = False, message_id: int | None = None):
    keywords = list_keywords()

    if not keywords:
        text = "🔑 <b>Keywords Manager</b>

No keywords configured yet.

Use the buttons below to add your first keyword!"
    else:
        lines = [f"🔑 <b>Keywords ({len(keywords)})</b>
"]
        for kw in keywords[:15]:
            lines.append(f"├─ <code>{_escape(kw['keyword'])}</code>")
            lines.append(f"│  └─ {_escape(kw['reply'][:50])}...")
        if len(keywords) > 15:
            lines.append(f"
<i>...and {len(keywords) - 15} more</i>")
        text = "
".join(lines)

    keyboard = _build_inline_keyboard([
        [("➕ Add Keyword", "kw:add"), ("➖ Remove Keyword", "kw:remove")],
        [("🔙 Back", "back:main")],
    ])

    if keywords:
        delete_buttons = [(f"🗑️ {_escape(kw['keyword'][:20])}", f"kw:del:{kw['keyword']}") for kw in keywords[:10]]
        rows = [delete_buttons[i:i+2] for i in range(0, len(delete_buttons), 2)]
        keyboard = _build_inline_keyboard(rows + [[("🔙 Back", "back:main")]])

    if edit and message_id:
        _edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send_message(chat_id, text, reply_markup=keyboard)


def show_settings_menu(chat_id: str, edit: bool = False, message_id: int | None = None):
    paused = is_bot_paused()
    gemini = is_gemini_enabled()
    safe = is_safe_mode()

    text = (
        f"⚙️ <b>Bot Settings</b>

"
        f"<b>Toggles:</b>
"
        f"├─ Bot Status: {'⏸️ PAUSED' if paused else '✅ RUNNING'}
"
        f"├─ Gemini AI: {'🟢 ENABLED' if gemini else '🔴 DISABLED'}
"
        f"└─ Safe Mode: {'🛡️ ACTIVE' if safe else '⚪ INACTIVE'}

"
        f"<i>Click a toggle to change its state:</i>"
    )

    pause_btn = "▶️ Resume Bot" if paused else "⏸️ Pause Bot"
    gemini_btn = "🔴 Disable Gemini" if gemini else "🟢 Enable Gemini"
    safe_btn = "🛡️ Disable Safe Mode" if safe else "🛡️ Enable Safe Mode"

    keyboard = _build_inline_keyboard([
        [(pause_btn, "toggle:pause")],
        [(gemini_btn, "toggle:gemini")],
        [(safe_btn, "toggle:safe")],
        [("🗑️ Clear AI Cache", "action:clearcache")],
        [("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send_message(chat_id, text, reply_markup=keyboard)


def show_activity_log(chat_id: str, edit: bool = False, message_id: int | None = None):
    activities = get_recent_activity(20)

    if not activities:
        text = "📝 <b>Activity Log</b>

No recent activity recorded."
    else:
        lines = ["📝 <b>Recent Activity (Last 20)</b>
"]
        for i, act in enumerate(activities, 1):
            action = act["action"]
            timestamp = act["created_at"]
            if isinstance(timestamp, datetime):
                time_str = timestamp.strftime("%H:%M %d/%m")
            else:
                time_str = str(timestamp)[:16]
            lines.append(f"{i}. <b>{_escape(action)}</b> @ {time_str}")
        text = "
".join(lines)

    keyboard = _build_inline_keyboard([
        [("🔄 Refresh", "refresh:activity"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send_message(chat_id, text, reply_markup=keyboard)


def show_health_check(chat_id: str, edit: bool = False, message_id: int | None = None):
    db_ok = db_ping()
    ai_status = get_ai_status()
    gemini_keys = ai_status.get("gemini", {}).get("keys", [])
    has_gemini = len(gemini_keys) > 0
    has_groq = ai_status.get("groq_enabled", False)

    checks = [
        ("🗄️ Database", db_ok),
        ("🧠 Gemini API", has_gemini),
        ("⚡ Groq Fallback", has_groq),
        ("🤖 Bot Running", not is_bot_paused()),
    ]

    all_ok = all(ok for _, ok in checks)
    status_icon = "✅" if all_ok else "⚠️"

    lines = [f"{status_icon} <b>System Health Check</b>
"]
    for name, ok in checks:
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} {name}")

    lines.append("
<b>Configuration:</b>")
    lines.append(f"├─ Cache Size: {ai_status.get('cache', {}).get('size', 0)}")
    lines.append(f"├─ Gemini Keys: {len(gemini_keys)}")
    lines.append(f"└─ Keywords: {list_keywords().__len__()}")

    text = "
".join(lines)

    keyboard = _build_inline_keyboard([
        [("🔄 Check Again", "menu:health"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send_message(chat_id, text, reply_markup=keyboard)


def show_logs(chat_id: str):
    text = (
        "📋 <b>System Logs</b>

"
        "Log viewing is available in the Render dashboard or server logs.

"
        "For real-time monitoring, check your deployment platform's logging interface."
    )
    keyboard = _build_inline_keyboard([[("🔙 Back", "back:main")]])
    _send_message(chat_id, text, reply_markup=keyboard)


def start_add_keyword_wizard(chat_id: str):
    _user_states[chat_id] = {"action": "add_keyword_step1"}
    text = (
        "➕ <b>Add New Keyword</b>

"
        "Please send the <b>keyword/phrase</b> you want to add.

"
        "<i>Example: krishna, radhe radhe, jai shri krishna</i>

"
        "Send /cancel to abort."
    )
    keyboard = _build_inline_keyboard([[("❌ Cancel", "back:keywords")]])
    _send_message(chat_id, text, reply_markup=keyboard)


def start_remove_keyword_wizard(chat_id: str):
    keywords = list_keywords()
    if not keywords:
        _send_message(chat_id, "❌ No keywords to remove.", reply_markup=_build_inline_keyboard([[("🔙 Back", "back:keywords")]]))
        return

    _user_states[chat_id] = {"action": "remove_keyword_step1"}
    text = (
        "➖ <b>Remove Keyword</b>

"
        "Send the exact <b>keyword</b> you want to remove:

"
    )
    kw_list = "
".join([f"• <code>{_escape(k['keyword'])}</code>" for k in keywords[:10]])
    text += kw_list
    if len(keywords) > 10:
        text += f"
<i>...and {len(keywords) - 10} more</i>"
    text += "

Send /cancel to abort."

    keyboard = _build_inline_keyboard([[("❌ Cancel", "back:keywords")]])
    _send_message(chat_id, text, reply_markup=keyboard)


# ==================== LEGACY FUNCTIONS (kept for backward compatibility) ====================

def _build_welcome_text() -> str:
    return "🦚 <b>KrishnaVerse AI Admin</b>

Use /help to view available commands."


def _build_help_text() -> str:
    return (
        "🦚 <b>Commands</b>

"
        "/status - bot and database status
"
        "/stats - today's stats
"
        "/pause - pause bot
"
        "/resume - resume bot
"
        "/ping - health check
"
        "/ai - AI status
"
        "/models - model status
"
        "/quota - quota summary
"
        "/keywords - list keywords
"
        "/addkeyword word reply - add keyword reply
"
        "/removekeyword word - remove keyword
"
        "/clearcache - clear AI cache
"
        "/gemini_on /gemini_off - toggle Gemini
"
        "/safe_on /safe_off - toggle safe mode"
    )


def _build_status_text() -> str:
    stats = get_stats()
    ai = get_ai_status()
    return (
        "🦚 <b>Bot Status</b>

"
        f"Paused: <b>{stats['bot_paused']}</b>
"
        f"Gemini Enabled: <b>{stats['gemini_enabled']}</b>
"
        f"Safe Mode: <b>{stats['safe_mode']}</b>
"
        f"Comments Replied: <b>{stats['comments_replied']}</b>
"
        f"Welcome DMs Sent: <b>{stats['welcome_dms_sent']}</b>
"
        f"AI Calls 24h: <b>{stats['ai_calls_24h']}</b>
"
        f"Cache Size: <b>{ai.get('cache', {}).get('size', 0)}</b>
"
        f"Gemini Keys: <b>{len(ai.get('gemini', {}).get('keys', []))}</b>
"
        f"Groq Enabled: <b>{ai['groq_enabled']}</b>"
    )


def _build_stats_text() -> str:
    stats = get_stats()
    return (
        "📊 <b>Today's Stats</b>

"
        f"Comments Replied: <b>{stats['comments_replied']}</b>
"
        f"Welcome DMs Sent: <b>{stats['welcome_dms_sent']}</b>
"
        f"Processed Events: <b>{stats['processed_events']}</b>
"
        f"AI Calls 24h: <b>{stats['ai_calls_24h']}</b>
"
        f"Keywords: <b>{stats['keywords']}</b>"
    )


def _build_ai_text() -> str:
    ai = get_ai_status()
    lines = ["🤖 <b>AI Status</b>"]

    gemini_data = ai.get("gemini", {})
    keys = gemini_data.get("keys", [])

    for key in keys:
        lines.append(f"
Key: <b>{_escape(key.get('key_id', 'unknown'))}</b>")
        lines.append(f"Requests Today: <b>{key.get('requests_today', 0)}</b>")
        lines.append(f"Quota Hits: <b>{key.get('quota_hits_today', 0)}</b>")
        lines.append("Models:")
        for model in key.get("models", []):
            lines.append(f" • {_escape(model)}")

    lines.append(f"
Groq Enabled: <b>{ai.get('groq_enabled', False)}</b>")
    lines.append(f"Cache Size: <b>{ai.get('cache', {}).get('size', 0)}</b>")
    return "
".join(lines)


def _build_keywords_text() -> str:
    rows = list_keywords()
    if not rows:
        return "No keywords configured."
    lines = ["🔑 <b>Keywords</b>"]
    for row in rows:
        lines.append(f"• <code>{_escape(row['keyword'])}</code> → {_escape(row['reply'][:80])}")
    return "
".join(lines)


def _build_activity_text() -> str:
    rows = get_recent_activity(10)
    if not rows:
        return "No recent activity."
    lines = ["📝 <b>Recent Activity</b>"]
    for row in rows:
        lines.append(f"• {_escape(row['action'])} @ {_escape(str(row['created_at']))}")
    return "
".join(lines)
