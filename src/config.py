from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    check_interval: int
    min_profit: float
    min_discount: float
    products_file: Path
    max_concurrency: int
    user_agent: str
    alert_cooldown_seconds: int
    alert_cache_file: Path
    verify_enabled: bool
    verify_delay_seconds: float
    verify_price_tolerance_percent: float
    min_confidence_score: int
    require_condition_match: bool
    allowed_conditions: tuple[str, ...]


def load_settings() -> Settings:
    load_dotenv()

    check_interval = int(os.getenv("CHECK_INTERVAL", "30"))
    min_profit = float(os.getenv("MIN_PROFIT", "300"))
    min_discount = float(os.getenv("MIN_DISCOUNT", "25"))
    max_concurrency = int(os.getenv("MAX_CONCURRENCY", "4"))
    alert_cooldown_seconds = int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800"))
    products_file = Path(os.getenv("PRODUCTS_FILE", "data/products.json"))
    alert_cache_file = Path(os.getenv("ALERT_CACHE_FILE", "data/alert_cache.json"))
    verify_enabled = os.getenv("VERIFY_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    verify_delay_seconds = float(os.getenv("VERIFY_DELAY_SECONDS", "4"))
    verify_price_tolerance_percent = float(os.getenv("VERIFY_PRICE_TOLERANCE_PERCENT", "7"))
    min_confidence_score = int(os.getenv("MIN_CONFIDENCE_SCORE", "70"))
    require_condition_match = os.getenv("REQUIRE_CONDITION_MATCH", "false").strip().lower() in {"1", "true", "yes", "on"}
    allowed_conditions_raw = os.getenv(
        "ALLOWED_CONDITIONS",
        "like new,very good,good,acceptable,yeni gibi,cok iyi,iyi,kabul edilebilir",
    )
    allowed_conditions = tuple(
        item.strip().lower() for item in allowed_conditions_raw.split(",") if item.strip()
    )
    user_agent = os.getenv(
        "SCRAPER_USER_AGENT",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
    )

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        check_interval=check_interval,
        min_profit=min_profit,
        min_discount=min_discount,
        products_file=products_file,
        max_concurrency=max_concurrency,
        user_agent=user_agent,
        alert_cooldown_seconds=alert_cooldown_seconds,
        alert_cache_file=alert_cache_file,
        verify_enabled=verify_enabled,
        verify_delay_seconds=verify_delay_seconds,
        verify_price_tolerance_percent=verify_price_tolerance_percent,
        min_confidence_score=min_confidence_score,
        require_condition_match=require_condition_match,
        allowed_conditions=allowed_conditions,
    )
