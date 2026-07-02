#!/usr/bin/env python3
"""
Setup Instagram Webhook Subscriptions

This script subscribes your Meta app to the necessary webhook fields
so your bot receives comment, message, and follow events.

Usage:
    python setup_webhook_subscriptions.py

Required Environment Variables:
    - PAGE_ID: Your Facebook Page ID
    - GRAPH_ACCESS_TOKEN: Your EAA... access token
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import SETTINGS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

BASE_GRAPH = "https://graph.facebook.com/v25.0"

SUBSCRIPTION_FIELDS = [
    "comments",
    "messages",
    "feed",
]


def subscribe_to_webhooks() -> bool:
    import requests

    page_id = SETTINGS.page_id
    token = SETTINGS.graph_access_token

    if not page_id:
        logger.error("PAGE_ID not configured")
        return False

    if not token:
        logger.error("GRAPH_ACCESS_TOKEN not configured")
        return False

    endpoint = f"{BASE_GRAPH}/{page_id}/subscriptions"

    logger.info("Subscribing to webhooks for Page ID: %s", page_id)
    logger.info("Fields to subscribe: %s", ", ".join(SUBSCRIPTION_FIELDS))

    success_count = 0

    for field in SUBSCRIPTION_FIELDS:
        try:
            resp = requests.post(
                endpoint,
                params={"access_token": token},
                data={
                    "subscribed_fields": field,
                    "callback_url": f"{SETTINGS.public_base_url}/webhook",
                    "verify_token": SETTINGS.verify_token,
                },
                timeout=30,
            )

            if resp.ok:
                logger.info("✓ Successfully subscribed to '%s'", field)
                success_count += 1
            else:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"error": resp.text}
                error_msg = str(error_data).lower()
                if "is already subscribed" in error_msg or "duplicate" in error_msg:
                    logger.info("✓ Already subscribed to '%s'", field)
                    success_count += 1
                else:
                    logger.error("✗ Failed to subscribe to '%s': %s", field, error_data)

        except Exception as e:
            logger.error("✗ Error subscribing to '%s': %s", field, e)

    logger.info("Subscription complete: %d/%d fields successful", success_count, len(SUBSCRIPTION_FIELDS))

    if success_count == len(SUBSCRIPTION_FIELDS):
        logger.info("All webhook subscriptions configured successfully!")
        logger.info("Your bot should now receive events for comments, messages, and follows.")
        return True
    else:
        logger.warning("Only %d/%d subscriptions succeeded.", success_count, len(SUBSCRIPTION_FIELDS))
        logger.warning("Some bot features may not work until all subscriptions are configured.")
        return False


def list_current_subscriptions() -> None:
    import requests

    page_id = SETTINGS.page_id
    token = SETTINGS.graph_access_token

    if not page_id or not token:
        logger.error("Missing PAGE_ID or GRAPH_ACCESS_TOKEN")
        return

    endpoint = f"{BASE_GRAPH}/{page_id}/subscriptions"

    try:
        resp = requests.get(
            endpoint,
            params={"access_token": token},
            timeout=30,
        )

        if resp.ok:
            data = resp.json()
            subscriptions = data.get("data", [])

            logger.info("Current webhook subscriptions for Page %s:", page_id)
            if subscriptions:
                for sub in subscriptions:
                    field = sub.get("object", "unknown")
                    callback = sub.get("callback_url", "N/A")
                    logger.info("  • %s → %s", field, callback)
            else:
                logger.info("  No active subscriptions found")
        else:
            logger.error("Failed to fetch subscriptions: %s - %s", resp.status_code, resp.text)

    except Exception as e:
        logger.error("Error fetching subscriptions: %s", e)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Setup Instagram Webhook Subscriptions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python setup_webhook_subscriptions.py              # Interactive mode
  python setup_webhook_subscriptions.py --subscribe  # Auto-subscribe without prompt
  python setup_webhook_subscriptions.py --list       # List current subscriptions only

Required Environment Variables:
  PAGE_ID                 Your Facebook Page ID
  GRAPH_ACCESS_TOKEN      Your EAA... access token with pages_manage_metadata permission
  PUBLIC_BASE_URL         Your bot's public URL (e.g., https://your-app.onrender.com)
  VERIFY_TOKEN            Your webhook verify token
        """
    )
    parser.add_argument("--subscribe", action="store_true", help="Subscribe to all webhook fields without prompting")
    parser.add_argument("--list", action="store_true", help="Only list current subscriptions, don't modify anything")

    args = parser.parse_args()

    print("=" * 60)
    print("Instagram Webhook Subscription Setup")
    print("=" * 60)
    print()

    logger.info("Checking current subscriptions...")
    list_current_subscriptions()
    print()

    if args.list:
        return

    if not args.subscribe:
        response = input("Do you want to subscribe to all required webhook fields? (y/n): ").strip().lower()

        if response not in ("y", "yes"):
            logger.info("Aborted by user")
            return

    print()
    success = subscribe_to_webhooks()

    print()
    if success:
        logger.info("Setup complete! Your bot should now work with auto-DM on comments.")
        logger.info("Next steps:")
        logger.info("1. Test by having someone comment on your Instagram post")
        logger.info("2. Check your Render logs for webhook events")
        logger.info("3. Verify the user receives both a comment reply and a welcome DM")
    else:
        logger.error("Setup incomplete. Please check the errors above.")
        logger.error("Troubleshooting tips:")
        logger.error("• Ensure GRAPH_ACCESS_TOKEN has 'pages_manage_metadata' permission")
        logger.error("• Verify PAGE_ID is correct (Facebook Page ID, not Instagram)")
        logger.error("• Make sure your PUBLIC_BASE_URL is accessible from the internet")
        logger.error("• Try regenerating your GRAPH_ACCESS_TOKEN in Meta Developer Console")
        logger.error("• Token may have expired - generate a new long-lived token")


if __name__ == "__main__":
    main()
