from __future__ import annotations

import sys
import requests


def setup(token: str, service_url: str, secret: str = None):
    url = service_url.rstrip("/") + "/telegram-webhook"
    payload = {"url": url, "allowed_updates": ["message", "callback_query"]}
    if secret:
        payload["secret_token"] = secret

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json=payload,
        timeout=10,
    )
    print(resp.json())


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python setup_telegram_webhook.py <TOKEN> <URL> [SECRET]")
    else:
        secret = sys.argv[3] if len(sys.argv) > 3 else None
        setup(sys.argv[1], sys.argv[2], secret)
