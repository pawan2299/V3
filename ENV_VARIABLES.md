# Environment Variables Configuration

## Required Variables (16 total)

### Application Config (3)
```
APP_ENV=production
LOG_LEVEL=INFO
PORT=10000
```

### Meta / Instagram Webhook (3)
```
VERIFY_TOKEN=your_verify_token_here
APP_SECRET=your_app_secret_here
PAGE_ID=your_facebook_page_id
```

### Meta / Instagram Access Tokens (2)
```
GRAPH_ACCESS_TOKEN=EAA...  # For comments/media operations
INSTAGRAM_LOGIN_ACCESS_TOKEN=IGG...  # For DM operations
```

### Instagram Bot Config (1)
```
OWN_ACCOUNT_ID=your_account_id  # To filter out your own comments
```

### Database (1)
```
DATABASE_URL=postgresql://user:password@host:port/database
```
> **Note:** When using Render Blueprint, `DATABASE_URL` is auto-injected via `fromDatabase`. You only need to set this manually for local development.

### Telegram Admin Bot (4)
```
TELEGRAM_BOT_TOKEN=bot_token_here
TELEGRAM_CHAT_ID=admin_chat_id
TELEGRAM_ADMIN_CHAT_IDS=id1,id2  # Additional admin IDs (comma-separated)
TELEGRAM_WEBHOOK_SECRET=your_secret  # Auto-generated if not provided
```

### AI Services (3)
```
GEMINI_API_KEYS=key1,key2,key3  # Comma-separated list
GROQ_API_KEY=groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile  # Optional, has default
```

### Performance / Cache (4)
```
AI_CACHE_TTL_SECONDS=1800  # Cache TTL in seconds (default: 1800)
AI_CACHE_MAXSIZE=2000      # Maximum cache size (default: 2000)
DB_POOL_MIN=2              # Minimum DB connections (default: 2)
DB_POOL_MAX=10             # Maximum DB connections (default: 10)
```

---

## Removed Variables (No longer needed)

The following variables have been removed and should be deleted from your Render configuration:

| Variable | Reason |
|----------|--------|
| `META_APP_ID` | Never used in code |
| `PAGE_ACCESS_TOKEN` | Redundant with `GRAPH_ACCESS_TOKEN` |
| `ACCESS_TOKEN` | Unnecessary fallback |
| `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3` | Use `GEMINI_API_KEYS` CSV instead |
| `GEMINI_API_KEY` | Single key fallback removed |
| `GEMINI_RPM_LIMIT` | Hardcoded in `gemini_client.py` |
| `GEMINI_SOFT_DAILY_FRACTION` | Not used |
| `MAX_IMAGE_BYTES` | Hardcoded to 4MB |
| `PUBLIC_BASE_URL` | Falls back to `RENDER_EXTERNAL_URL` automatically |

---

## Naming Convention

All environment variables follow **SCREAMING_SNAKE_CASE** (UPPERCASE with underscores):
- ✅ `GRAPH_ACCESS_TOKEN`
- ✅ `TELEGRAM_BOT_TOKEN`
- ❌ `graphAccessToken`
- ❌ `telegram-bot-token`

This follows the [12-Factor App config principle](https://12factor.net/config) and is the global standard for environment variables across all platforms (Render, Heroku, AWS, Docker, etc.).

---

## Token Usage Summary

### GRAPH_ACCESS_TOKEN (EAA...)
Used for:
- Replying to Instagram comments
- Fetching media URLs
- Token validity checks
- **Setting up webhook subscriptions** (critical!)

**Required permissions:**
- `pages_manage_metadata` — To manage webhook subscriptions
- `pages_show_list` — To access page information
- `instagram_basic` — To read Instagram account info
- `instagram_manage_messages` — To send DMs via webhook events

### INSTAGRAM_LOGIN_ACCESS_TOKEN (IGG...)
Used for:
- Sending welcome DMs to new followers
- Token validity checks for DM functionality

**Required permissions:**
- `instagram_basic`
- `instagram_manage_messages`

---

## Migration Steps

### 1. Update Render Environment Variables

In your Render Dashboard:
- Remove all deprecated variables listed above
- Keep only the variables listed in the **Required Variables** section
- Ensure all names are in `SCREAMING_SNAKE_CASE`

### 2. Database Setup (Render Blueprint)

If using `render.yaml` Blueprint:
- The `DATABASE_URL` is automatically injected from the `krishnaverse-db` PostgreSQL database
- No manual configuration needed

For local development, set `DATABASE_URL` in your `.env` file.

### 3. Update Token Strategy

- Set `GRAPH_ACCESS_TOKEN` to your EAA... token (for comments/media)
  - Ensure it has `pages_manage_metadata` permission for webhook setup
  - Ensure it has `instagram_manage_messages` permission for DMs
- Set `INSTAGRAM_LOGIN_ACCESS_TOKEN` to your IGG... token (for DMs)
- Remove `PAGE_ACCESS_TOKEN` and `ACCESS_TOKEN`

### 4. Consolidate Gemini Keys

- Combine all Gemini keys into `GEMINI_API_KEYS` as comma-separated values
- Example: `GEMINI_API_KEYS=key1,key2,key3`
- Remove individual `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, etc.

### 5. Test

- Restart your Render service
- Verify bot responds to comments
- Verify DMs are sent to new followers
- Check Telegram bot commands work
