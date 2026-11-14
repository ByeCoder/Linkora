import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


@dataclass
class Config:
    token: str
    owners: set[int]
    database: str = "data/bot.sqlite3"
    timezone: str = "Asia/Tehran"
    base_url: str = "https://waybien.com"
    api_url: str = "https://dashboard.waybien.com"
    cache_seconds: int = 120
    interval: int = 3
    daily_limit: int = 200

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        owners = {int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()}
        if not token or token.startswith("PUT_") or not owners or any(uid < 1 for uid in owners):
            raise ValueError("BOT_TOKEN و OWNER_IDS را در فایل .env تنظیم کنید.")
        tz = os.getenv("TIMEZONE", "Asia/Tehran")
        ZoneInfo(tz)
        return cls(token, owners, os.getenv("DATABASE_PATH", "data/bot.sqlite3"), tz,
                   os.getenv("WAYBIEN_BASE_URL", "https://waybien.com").rstrip("/"),
                   os.getenv("WAYBIEN_API_URL", "https://dashboard.waybien.com").rstrip("/"),
                   max(0, int(os.getenv("SEARCH_CACHE_SECONDS", "120"))),
                   max(1, int(os.getenv("SEARCH_INTERVAL_SECONDS", "3"))),
                   max(1, int(os.getenv("SEARCH_DAILY_LIMIT", "200"))))
