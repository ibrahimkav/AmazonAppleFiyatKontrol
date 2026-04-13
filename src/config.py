from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_ids: tuple[str, ...]
    check_interval: int
    products_file: Path
    max_concurrency: int
    user_agent: str
    alert_cooldown_seconds: int
    alert_cache_file: Path
    default_alert_below_try: float
    default_alert_discount_try: float
    quiet_threshold_skips: bool


def load_settings() -> Settings:
    load_dotenv()

    check_interval = int(os.getenv("CHECK_INTERVAL", "300"))
    max_concurrency = int(os.getenv("MAX_CONCURRENCY", "4"))
    alert_cooldown_seconds = int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800"))
    products_file = Path(os.getenv("PRODUCTS_FILE", "data/products.json"))
    alert_cache_file = Path(os.getenv("ALERT_CACHE_FILE", "data/alert_cache.json"))
    user_agent = os.getenv(
        "SCRAPER_USER_AGENT",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
    )
    default_alert_below_try = float(os.getenv("DEFAULT_ALERT_BELOW_TRY", "0"))
    default_alert_discount_try = float(os.getenv("DEFAULT_ALERT_DISCOUNT_TRY", "0"))
    quiet_threshold_skips = os.getenv("QUIET_THRESHOLD_SKIPS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    raw_chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    if not raw_chat_ids:
        raw_chat_ids = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    telegram_chat_ids = tuple(
        chat_id for chat_id in re.split(r"[,\s;]+", raw_chat_ids) if chat_id
    )

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_ids=telegram_chat_ids,
        check_interval=check_interval,
        products_file=products_file,
        max_concurrency=max_concurrency,
        user_agent=user_agent,
        alert_cooldown_seconds=alert_cooldown_seconds,
        alert_cache_file=alert_cache_file,
        default_alert_below_try=default_alert_below_try,
        default_alert_discount_try=default_alert_discount_try,
        quiet_threshold_skips=quiet_threshold_skips,
    )
