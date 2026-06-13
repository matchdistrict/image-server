import os
from dotenv import load_dotenv

load_dotenv()

# Server Settings
DOMAIN = os.getenv("DOMAIN", "https://tg-image-url-bot-production.up.railway.app").rstrip("/")
WEB_PORT = int(os.getenv("PORT") or os.getenv("WEB_PORT", "8000"))
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-change-in-production")
LOGO_URL = os.getenv("LOGO_URL", "https://tg-image-url-bot-production.up.railway.app/raw/5zzogd")

# Telegram Bot Settings
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "picturemaniabot").replace("@", "")
raw_channel_id = os.getenv("STORAGE_CHANNEL_ID") or os.getenv("CHANNEL_ID")
if raw_channel_id:
    try:
        STORAGE_CHANNEL_ID = int(raw_channel_id)
    except ValueError:
        STORAGE_CHANNEL_ID = raw_channel_id
else:
    STORAGE_CHANNEL_ID = None

# Telegram MTProto Settings (Telethon — for direct channel uploads/downloads)
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELETHON_SESSION_STRING = os.getenv("TELETHON_SESSION_STRING", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not configured in the environment variables!")

if not STORAGE_CHANNEL_ID:
    raise ValueError("STORAGE_CHANNEL_ID is not configured in the environment variables!")

# Database Settings
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Local fallback to SQLite. If a persistent volume is mounted at /data on Railway, use it.
    if os.path.exists("/data") and os.path.isdir("/data"):
        DATABASE_URL = "sqlite+aiosqlite:////data/images_db.sqlite"
    else:
        DATABASE_URL = "sqlite+aiosqlite:///./images_db.sqlite"
else:
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

# Redis Caching Settings
REDIS_URL = os.getenv("REDIS_URL", "")

# Rate Limiting Settings
GUEST_LIMIT = int(os.getenv("GUEST_LIMIT", "20"))
USER_LIMIT = int(os.getenv("USER_LIMIT", "500"))

# Telegram Admin IDs (comma-separated integers in environment)
raw_admin_ids = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = [
    int(x.strip())
    for x in raw_admin_ids.split(",")
    if x.strip().lstrip("-").isdigit()
]
