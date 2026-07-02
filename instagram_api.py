from __future__ import annotations

import logging
from typing import Optional

import requests

from config import SETTINGS

logger = logging.getLogger(__name__)
_session = requests.Session()
BASE_GRAPH = "https://graph.facebook.com/v25.0"
BASE_IG = "https://graph.instagram.com/v25.0"


def _extract_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return data.get("error", {}).get("message") or resp.text
    except Exception:
        return resp.text


def _facebook_post(endpoint: str, payload: dict, token: str) -> bool:
    try:
        resp = _session.post(
            f"{BASE_GRAPH}/{endpoint}",
            params={"access_token": token},
            json=payload,
            timeout=(10, 30),
        )
        if resp.ok:
            return True
        logger.error("Instagram API error %s: %s", resp.status_code, _extract_error(resp))
        return False
    except Exception:
        logger.exception("Instagram request failed")
        return False


def reply_to_comment(comment_id: str, message: str) -> bool:
    token = SETTINGS.graph_access_token
    if not token:
        logger.error("No Graph API token configured for comment replies")
        return False
    return _facebook_post(f"{comment_id}/replies", {"message": message}, token)


def send_dm(user_id: str, message: str) -> bool:
    token = SETTINGS.instagram_login_access_token
    if not token:
        logger.error("INSTAGRAM_LOGIN_ACCESS_TOKEN missing; fixed DM disabled")
        return False

    try:
        resp = _session.post(
            f"{BASE_IG}/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "recipient": {"id": user_id},
                "message": {"text": message},
            },
            timeout=(10, 30),
        )
        if resp.ok:
            return True
        logger.error("Instagram DM error %s: %s", resp.status_code, _extract_error(resp))
        return False
    except Exception:
        logger.exception("Instagram DM request failed")
        return False


def get_media_url(media_id: str) -> str | None:
    token = SETTINGS.graph_access_token
    if not token:
        return None
    try:
        resp = _session.get(
            f"{BASE_GRAPH}/{media_id}",
            params={"fields": "media_url,permalink", "access_token": token},
            timeout=(10, 20),
        )
        if not resp.ok:
            logger.warning("Failed to fetch media_url %s: %s", resp.status_code, _extract_error(resp))
            return None
        return resp.json().get("media_url")
    except Exception:
        logger.exception("Failed to fetch media_url")
        return None


def check_token_validity(token_type: str = "graph") -> bool:
    """
    Check if the configured access token is still valid.

    We use the /me endpoint for a basic validity check because it's more reliable
    than /debug_token for different types of tokens (Graph vs. Instagram Login)
    and doesn't require matching the App Secret exactly to the token's origin app.
    """
    token = None
    base_url = BASE_GRAPH

    if token_type == "graph":
        token = SETTINGS.graph_access_token
        base_url = BASE_GRAPH
    elif token_type == "dm":
        token = SETTINGS.instagram_login_access_token
        base_url = BASE_IG
    else:
        token = SETTINGS.graph_access_token
        base_url = BASE_GRAPH

    if not token:
        logger.warning("No token configured for token check (%s)", token_type)
        return False

    try:
        resp = _session.get(
            f"{base_url}/me",
            params={"access_token": token, "fields": "id"},
            timeout=(10, 20),
        )

        if resp.ok:
            logger.info("%s token is valid (verified via /me)", token_type)
            return True

        err_msg = _extract_error(resp)
        logger.error("%s token validity check failed (Status: %s): %s", token_type, resp.status_code, err_msg)
        return False

    except Exception:
        logger.exception("Token validity check failed for %s", token_type)
        return False
