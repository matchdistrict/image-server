import os
from dotenv import load_dotenv

load_dotenv()

# Server Settings
DOMAIN = os.getenv("DOMAIN", "https://tg-image-url-bot-production.up.railway.app").rstrip("/")
WEB_PORT = int(os.getenv("PORT") or os.getenv("WEB_PORT", "8000"))
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-change-in-production")

# Telegram Bot Settings
BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID") or os.getenv("CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not configured in the environment variables!")

if not STORAGE_CHANNEL_ID:
    raise ValueError("STORAGE_CHANNEL_ID is not configured in the environment variables!")

# Database Settings
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Local fallback to SQLite if PostgreSQL is not specified
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
USER_LIMIT = int(os.getenv("USER_LIMIT", "100"))
