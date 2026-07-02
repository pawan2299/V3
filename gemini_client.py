from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from typing import Dict, List, Optional

import requests
from PIL import Image
from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import SETTINGS
from database import (
    bump_model_usage,
    bump_provider_usage,
    get_daily_counter,
    get_key_requests_today,
    get_model_usage_today,
    increment_daily_counter,
    is_key_on_cooldown,
    is_model_on_cooldown,
    log_ai_usage,
    set_key_cooldown,
    set_model_cooldown,
)
from prompts import COMMENT_PROMPT

logger = logging.getLogger(__name__)

# Valid Gemini models as of June 2026 (verified from Google AI docs)
# Source: https://ai.google.dev/gemini-api/docs/models
MODEL_FALLBACK_CHAIN = [
    # Primary: Gemini 3.5 Flash (GA, flagship, 15 RPM, 1,500 RPD, 1M context)
    {"name": "gemini-3.5-flash", "max_safe_rpm": 12, "max_tpm": 1000000, "max_rpd": 1500, "priority": 200},
    # Secondary: Gemini 3.1 Flash-Lite (GA, ultra-fast, 15 RPM, 1,500 RPD)
    {"name": "gemini-3.1-flash-lite", "max_safe_rpm": 12, "max_tpm": 1000000, "max_rpd": 1500, "priority": 180},
    # Tertiary: Gemini 3 Flash Preview (preview, 15 RPM, 1,500 RPD)
    {"name": "gemini-3-flash-preview", "max_safe_rpm": 12, "max_tpm": 1000000, "max_rpd": 1500, "priority": 160},
    # Legacy: Gemini 2.5 Flash (stable, 15 RPM, 1,500 RPD)
    {"name": "gemini-2.5-flash", "max_safe_rpm": 12, "max_tpm": 1000000, "max_rpd": 1500, "priority": 100},
    # Emergency: Gemini 1.5 Flash (stable, 15 RPM, 1,500 RPD)
    {"name": "gemini-1.5-flash", "max_safe_rpm": 12, "max_tpm": 500000, "max_rpd": 1500, "priority": 50},
]

# Circuit breaker configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 300  # 5 minutes


@dataclass
class ModelInfo:
    key_id: str
    api_key: str
    project_id: str
    name: str
    priority: int
    rpm: int
    tpm: int
    rpd: int
    rpd_count: int = 0


_clients: Dict[str, genai.Client] = {}
_model_rpm_calls: Dict[tuple[str, str], deque[float]] = {}
_model_tpm_usage: Dict[tuple[str, str], deque[tuple[float, int]]] = {}
_model_rpd_calls: Dict[tuple[str, str], deque[str]] = {}
_circuit_breaker_failures: Dict[str, deque[float]] = {}


def _get_client(api_key: str) -> genai.Client:
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


def _current_time() -> float:
    return time.time()


def _get_today() -> str:
    return time.strftime("%Y-%m-%d")


def _generate_project_id(api_key: str) -> str:
    return hashlib.md5(api_key.encode()).hexdigest()[:8]


def _rpm_ok(key_id: str, model_name: str, rpm_limit: int) -> bool:
    calls = _model_rpm_calls.setdefault((key_id, model_name), deque())
    current = _current_time()
    while calls and (current - calls[0]) > 60:
        calls.popleft()
    return len(calls) < rpm_limit


def _record_rpm(key_id: str, model_name: str) -> None:
    calls = _model_rpm_calls.setdefault((key_id, model_name), deque())
    calls.append(_current_time())


def _rpd_ok(key_id: str, model_name: str, rpd_limit: int) -> bool:
    today = _get_today()
    calls = _model_rpd_calls.setdefault((key_id, model_name), deque())
    while calls and calls[0] != today:
        calls.popleft()
        if not calls:
            break
    return len(calls) < rpd_limit


def _record_rpd(key_id: str, model_name: str) -> None:
    today = _get_today()
    calls = _model_rpd_calls.setdefault((key_id, model_name), deque())
    while calls and calls[0] != today:
        calls.popleft()
        if not calls:
            break
    calls.append(today)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _tpm_ok(key_id: str, model_name: str, tpm_limit: int, estimated_tokens: int) -> bool:
    usage = _model_tpm_usage.setdefault((key_id, model_name), deque())
    current = _current_time()
    while usage and (current - usage[0][0]) > 60:
        usage.popleft()
    current_tpm = sum(tokens for _, tokens in usage)
    return (current_tpm + estimated_tokens) <= tpm_limit


def _record_tpm(key_id: str, model_name: str, tokens: int) -> None:
    usage = _model_tpm_usage.setdefault((key_id, model_name), deque())
    usage.append((_current_time(), tokens))


def _is_quota_error(error: Exception) -> bool:
    if isinstance(error, APIError):
        return error.code == 429
    err_str = str(error).lower()
    return "quota" in err_str or "resource_exhausted" in err_str or "429" in err_str


def _record_circuit_breaker_failure(model_key: str) -> None:
    failures = _circuit_breaker_failures.setdefault(model_key, deque())
    failures.append(_current_time())
    cutoff = _current_time() - CIRCUIT_BREAKER_RECOVERY_TIMEOUT
    while failures and failures[0] < cutoff:
        failures.popleft()


def _is_circuit_open(model_key: str) -> bool:
    failures = _circuit_breaker_failures.get(model_key, deque())
    cutoff = _current_time() - CIRCUIT_BREAKER_RECOVERY_TIMEOUT
    while failures and failures[0] < cutoff:
        failures.popleft()
    return len(failures) >= CIRCUIT_BREAKER_FAILURE_THRESHOLD


def _maybe_cooldown(key_id: str, model_name: str, error: Exception) -> None:
    if _is_quota_error(error):
        set_model_cooldown(f"{key_id}:{model_name}", _current_time() + 23 * 3600)
        increment_daily_counter("gemini_quota_hits", f"{key_id}:{model_name}")
        logger.warning("Gemini quota hit for %s / %s", key_id, model_name)


def generate_comment_reply(comment_text: str, post_caption: str = "", image_url: str | None = None,
                           max_output_tokens: int = 220) -> Optional[str]:
    if not SETTINGS.gemini_api_keys:
        return None

    sanitized_comment = re.sub(r'[^\w\s.,!?@#'"-]', '', comment_text.strip())
    sanitized_caption = re.sub(r'[^\w\s.,!?@#'"-]', '', post_caption.strip()) if post_caption else ""

    context = ""
    if sanitized_caption:
        context = f"
Post context: {sanitized_caption[:300]}"
    prompt = (
        COMMENT_PROMPT
        + context
        + "
Comment: "
        + sanitized_comment
        + "
Write only the reply text."
    )

    estimated_input_tokens = _estimate_tokens(prompt)
    estimated_output_tokens = max_output_tokens // 2
    total_estimated_tokens = estimated_input_tokens + estimated_output_tokens

    candidates: List[ModelInfo] = []
    for idx, api_key in enumerate(SETTINGS.gemini_api_keys, start=1):
        key_id = f"key_{idx}"
        if is_key_on_cooldown(key_id):
            continue

        project_id = _generate_project_id(api_key)

        for model_meta in MODEL_FALLBACK_CHAIN:
            candidates.append(ModelInfo(
                key_id=key_id,
                api_key=api_key,
                project_id=project_id,
                name=model_meta["name"],
                priority=model_meta["priority"],
                rpm=model_meta["max_safe_rpm"],
                tpm=model_meta.get("max_tpm", 500000),
                rpd=model_meta.get("max_rpd", 1500)
            ))

    candidates.sort(key=lambda m: (m.priority, random.random()), reverse=True)

    for candidate in candidates:
        model_key = f"{candidate.key_id}:{candidate.name}"

        if _is_circuit_open(model_key):
            logger.debug("Circuit breaker open for %s, skipping", model_key)
            continue

        if is_model_on_cooldown(model_key):
            continue
        if not _rpm_ok(candidate.key_id, candidate.name, candidate.rpm):
            logger.debug("RPM limit reached for %s / %s", candidate.key_id, candidate.name)
            continue
        if not _rpd_ok(candidate.key_id, candidate.name, candidate.rpd):
            logger.debug("RPD limit reached for %s / %s (%d/%d)",
                        candidate.key_id, candidate.name,
                        len(_model_rpd_calls.get((candidate.key_id, candidate.name), deque())),
                        candidate.rpd)
            continue
        if not _tpm_ok(candidate.key_id, candidate.name, candidate.tpm, total_estimated_tokens):
            logger.debug("TPM limit reached for %s / %s", candidate.key_id, candidate.name)
            continue

        try:
            client = _get_client(candidate.api_key)
            _record_rpm(candidate.key_id, candidate.name)
            _record_rpd(candidate.key_id, candidate.name)

            start = _current_time()

            contents = [prompt]
            if image_url:
                try:
                    resp = requests.get(image_url, timeout=(3, 10))
                    resp.raise_for_status()
                    img = Image.open(BytesIO(resp.content))

                    if img.width > 768 or img.height > 768:
                        img.thumbnail((768, 768))

                    contents.append(img)
                except Exception as e:
                    logger.warning("Failed to process image %s: %s", image_url, e)

            max_retries = 3
            base_retry_delay = 0.5
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    response = client.models.generate_content(
                        model=candidate.name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            max_output_tokens=max_output_tokens,
                            temperature=0.5,
                            top_p=0.9,
                        )
                    )

                    latency = int((_current_time() - start) * 1000)

                    if response.prompt_feedback and response.prompt_feedback.block_reason:
                        logger.warning("Gemini safety filter blocked request: %s",
                                     response.prompt_feedback.block_reason)
                        _record_circuit_breaker_failure(model_key)
                        break

                    if response.text:
                        actual_output_tokens = _estimate_tokens(response.text)
                        _record_tpm(candidate.key_id, candidate.name,
                                  estimated_input_tokens + actual_output_tokens)

                        log_ai_usage("gemini", candidate.name, "success", latency_ms=latency)
                        bump_model_usage(candidate.key_id, candidate.name, True)
                        bump_provider_usage("gemini", True)
                        return response.text.strip()[:500]
                    else:
                        log_ai_usage("gemini", candidate.name, "empty", latency_ms=latency)
                        _record_circuit_breaker_failure(model_key)
                        break

                except APIError as api_err:
                    last_error = api_err
                    if api_err.code == 429:
                        _record_circuit_breaker_failure(model_key)
                        logger.warning("Rate limit hit for %s / %s", candidate.key_id, candidate.name)
                        break
                    elif api_err.code in [500, 502, 503, 504]:
                        if attempt < max_retries:
                            jitter = random.uniform(0, 0.5)
                            total_delay = base_retry_delay * (2 ** attempt) + jitter
                            logger.warning("Transient error on attempt %d, retrying in %.2fs: %s",
                                         attempt + 1, total_delay, str(api_err)[:200])
                            time.sleep(total_delay)
                            continue
                        else:
                            _record_circuit_breaker_failure(model_key)
                            logger.error("Max retries exceeded for transient error on %s", model_key)
                            break
                    else:
                        _record_circuit_breaker_failure(model_key)
                        logger.error("API error %d for %s: %s", api_err.code, model_key, str(api_err)[:300])
                        break
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        jitter = random.uniform(0, 0.5)
                        total_delay = base_retry_delay * (2 ** attempt) + jitter
                        logger.warning("Retryable error on attempt %d, retrying in %.2fs: %s",
                                     attempt + 1, total_delay, str(e)[:200])
                        time.sleep(total_delay)
                        continue
                    else:
                        _record_circuit_breaker_failure(model_key)
                        logger.error("Max retries exceeded for error on %s", model_key)
                        break

            latency = int((_current_time() - start) * 1000)
            log_ai_usage("gemini", candidate.name, f"error_{type(last_error).__name__}", latency_ms=latency)
            bump_model_usage(candidate.key_id, candidate.name, False)
            bump_provider_usage("gemini", False)

            if last_error and _is_quota_error(last_error):
                _maybe_cooldown(candidate.key_id, candidate.name, last_error)
            else:
                set_model_cooldown(model_key, _current_time() + 900)

            logger.warning("Gemini model %s failed for %s after retries: %s",
                         candidate.name, candidate.key_id, str(last_error)[:300] if last_error else "unknown")
            continue

        except Exception as exc:
            latency = int((_current_time() - start) * 1000)
            log_ai_usage("gemini", candidate.name, f"error_{type(exc).__name__}", latency_ms=latency)
            bump_model_usage(candidate.key_id, candidate.name, False)
            bump_provider_usage("gemini", False)
            _record_circuit_breaker_failure(model_key)

            if _is_quota_error(exc):
                _maybe_cooldown(candidate.key_id, candidate.name, exc)
            else:
                set_model_cooldown(model_key, _current_time() + 900)

            logger.warning("Gemini model %s failed for %s: %s", candidate.name, candidate.key_id, str(exc)[:300])
            continue

    return None


def is_spam_or_negative(text: str) -> bool:
    lower = text.lower()
    bad_signals = ("spam", "scam", "follow me", "dm me", "promo", "crypto", "giveaway", "link in bio")
    if any(signal in lower for signal in bad_signals):
        return True
    if len(set(lower.replace(" ", ""))) <= 2 and len(lower) > 10:
        return True
    return False


def get_status() -> dict:
    keys = []
    for idx, api_key in enumerate(SETTINGS.gemini_api_keys, start=1):
        key_id = f"key_{idx}"
        project_id = _generate_project_id(api_key)
        model_usage = get_model_usage_today(key_id)

        keys.append({
            "key_id": key_id,
            "project_id": project_id,
            "enabled": not is_key_on_cooldown(key_id),
            "requests_today": get_key_requests_today(key_id),
            "quota_hits_today": get_daily_counter("gemini_quota_hits", key_id),
            "models": [
                {
                    "name": m["name"],
                    "rpm_limit": m["max_safe_rpm"],
                    "tpm_limit": m.get("max_tpm", 500000),
                    "rpd_limit": m.get("max_rpd", 1500),
                    "usage_today": model_usage.get(m["name"], {}).get("requests_today", 0),
                    "success_rate": _calculate_success_rate(model_usage.get(m["name"], {})),
                    "circuit_open": _is_circuit_open(f"{key_id}:{m['name']}"),
                }
                for m in MODEL_FALLBACK_CHAIN
            ],
        })

    return {
        "keys": keys,
        "model_count": len(MODEL_FALLBACK_CHAIN) * len(SETTINGS.gemini_api_keys),
        "cache_status": "managed elsewhere",
        "groq_fallback": bool(SETTINGS.groq_api_key),
        "circuit_breaker_threshold": CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        "circuit_breaker_timeout": CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
        "multi_project_enabled": len(SETTINGS.gemini_api_keys) > 1,
        "total_daily_capacity": len(SETTINGS.gemini_api_keys) * 1500,
    }


def _calculate_success_rate(usage: dict) -> float:
    if not usage:
        return 0.0
    total = usage.get("success_count", 0) + usage.get("failure_count", 0)
    if total == 0:
        return 0.0
    return (usage.get("success_count", 0) / total) * 100
