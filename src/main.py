from __future__ import annotations

import asyncio
import html
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

from analyzer import analyze_prices, is_profitable
from config import load_settings
from notifier import TelegramNotifier
from scraper import AmazonScraper, ProductSnapshot


def _log(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        # Fallback for Windows terminals with non-UTF codepages.
        safe = message.encode("cp1254", errors="replace").decode("cp1254", errors="replace")
        sys.stdout.write(f"{safe}\n")
        sys.stdout.flush()


def load_products(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Products file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    products: list[dict[str, str]] = []
    for item in raw:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if name and url:
            products.append({"name": name, "url": url})
    return products


def load_alert_cache(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    cache: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str):
                try:
                    cache[key] = float(value)
                except (TypeError, ValueError):
                    continue
    return cache


def save_alert_cache(path: Path, cache: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_product_url(url: str) -> str:
    dp_match = re.search(r"/dp/([A-Z0-9]{10})", url, flags=re.IGNORECASE)
    if dp_match:
        asin = dp_match.group(1).upper()
        return f"https://www.amazon.com.tr/dp/{asin}"
    return url.split("?")[0]


def format_alert(snapshot: ProductSnapshot, profit: float, discount: float, confidence_score: int) -> str:
    safe_title = html.escape(snapshot.title)
    safe_condition = html.escape(snapshot.warehouse_condition or "Unknown")
    warehouse_price = f"{snapshot.warehouse_price:.2f} TL"
    normal_price = f"{snapshot.normal_price:.2f} TL"
    return (
        "🔥 <b>DEAL FOUND</b>\n\n"
        f"🛍️ <b>{safe_title}</b>\n"
        f"📦 Condition: <b>{safe_condition}</b>\n\n"
        f"💰 Warehouse: <b>{warehouse_price}</b>\n"
        f"🏷️ Normal: <b>{normal_price}</b>\n"
        f"📈 Profit: <b>{profit:.2f} TL</b>\n"
        f"🎯 Discount: <b>%{discount:.1f}</b>\n\n"
        f"✅ Confidence: <b>{confidence_score}/100</b>\n"
        f"🧠 Signals: n={snapshot.normal_price_source_count}, "
        f"w={snapshot.warehouse_price_source_count}, c={snapshot.condition_confidence}"
    )


def format_startup_message(*, product_count: int, settings: Any) -> str:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    return (
        "✅ BOT ACTIVE\n\n"
        "Amazon Warehouse Arbitrage bot is running.\n\n"
        f"Started: {started_at}\n"
        f"Tracked products: {product_count}\n"
        f"Check interval: {settings.check_interval}s\n"
        f"Min profit: {settings.min_profit:.0f} TL\n"
        f"Min discount: %{settings.min_discount:.0f}\n"
        f"Min confidence: {settings.min_confidence_score}/100"
    )


def format_shutdown_message(*, product_count: int) -> str:
    stopped_at = time.strftime("%Y-%m-%d %H:%M:%S")
    return (
        "🛑 BOT STOPPED\n\n"
        "Amazon Warehouse Arbitrage bot has stopped.\n\n"
        f"Stopped: {stopped_at}\n"
        f"Tracked products: {product_count}"
    )


def _alert_key(snapshot: ProductSnapshot) -> str:
    warehouse = f"{snapshot.warehouse_price:.2f}" if snapshot.warehouse_price is not None else "na"
    normal = f"{snapshot.normal_price:.2f}" if snapshot.normal_price is not None else "na"
    return f"{snapshot.url}|w:{warehouse}|n:{normal}"


def _should_send_alert(
    *,
    key: str,
    sent_cache: dict[str, float],
    now_ts: float,
    cooldown_seconds: int,
) -> bool:
    last_sent = sent_cache.get(key)
    if last_sent is None:
        return True
    return (now_ts - last_sent) >= cooldown_seconds


def _prune_cache(sent_cache: dict[str, float], now_ts: float, cooldown_seconds: int) -> None:
    expire_before = now_ts - (cooldown_seconds * 2)
    stale_keys = [k for k, ts in sent_cache.items() if ts < expire_before]
    for key in stale_keys:
        sent_cache.pop(key, None)


def _price_delta_percent(base: float, other: float) -> float:
    if base <= 0:
        return 100.0
    return abs(base - other) / base * 100


def _is_snapshot_consistent(
    *,
    first: ProductSnapshot,
    second: ProductSnapshot,
    tolerance_percent: float,
) -> bool:
    if first.normal_price is None or first.warehouse_price is None:
        return False
    if second.normal_price is None or second.warehouse_price is None:
        return False

    normal_delta = _price_delta_percent(first.normal_price, second.normal_price)
    warehouse_delta = _price_delta_percent(first.warehouse_price, second.warehouse_price)
    condition_match = (first.warehouse_condition or "").strip().lower() == (second.warehouse_condition or "").strip().lower()

    return normal_delta <= tolerance_percent and warehouse_delta <= tolerance_percent and condition_match


def _calculate_confidence_score(
    *,
    snapshot: ProductSnapshot,
    verify_snapshot: ProductSnapshot | None,
    verify_enabled: bool,
    tolerance_percent: float,
    profit: float,
    discount_percent: float,
) -> int:
    score = 35

    condition = (snapshot.warehouse_condition or "").strip().lower()
    if "like new" in condition:
        score += 20
    elif "very good" in condition:
        score += 15
    elif "good" in condition:
        score += 8
    elif "acceptable" in condition:
        score += 4

    score += min(20, snapshot.normal_price_source_count * 5)
    score += min(20, snapshot.warehouse_price_source_count * 6)
    score += min(15, max(0, snapshot.condition_confidence // 6))

    if discount_percent >= 45:
        score += 8
    elif discount_percent >= 35:
        score += 6
    elif discount_percent >= 25:
        score += 4

    if profit >= 1500:
        score += 6
    elif profit >= 800:
        score += 4
    elif profit >= 300:
        score += 2

    if verify_enabled and verify_snapshot is not None:
        score += 10
        if snapshot.normal_price and verify_snapshot.normal_price:
            normal_delta = _price_delta_percent(snapshot.normal_price, verify_snapshot.normal_price)
            if normal_delta <= tolerance_percent * 0.5:
                score += 10
            elif normal_delta <= tolerance_percent:
                score += 5
        if snapshot.warehouse_price and verify_snapshot.warehouse_price:
            warehouse_delta = _price_delta_percent(snapshot.warehouse_price, verify_snapshot.warehouse_price)
            if warehouse_delta <= tolerance_percent * 0.5:
                score += 10
            elif warehouse_delta <= tolerance_percent:
                score += 5
    elif not verify_enabled:
        score -= 10

    return max(0, min(100, score))


def _missing_parts(snapshot: ProductSnapshot) -> str:
    missing: list[str] = []
    if snapshot.normal_price is None:
        missing.append("normal_price")
    if snapshot.warehouse_price is None:
        missing.append("warehouse_price")
    return ", ".join(missing) if missing else "none"


async def process_product(
    *,
    scraper: AmazonScraper,
    notifier: TelegramNotifier,
    product: dict[str, str],
    settings: Any,
    sent_cache: dict[str, float],
) -> None:
    name = product["name"]
    url = product["url"]
    try:
        snapshot = await scraper.fetch_product(name, url)
    except Exception as exc:
        _log(f"[Scraper] Failed {name}: {exc}")
        return

    if snapshot.warehouse_price is None or snapshot.normal_price is None:
        _log(
            f"[Analyzer] Missing prices for {snapshot.title} | "
            f"missing={_missing_parts(snapshot)} | url={snapshot.url}"
        )
        return

    analysis = analyze_prices(snapshot.normal_price, snapshot.warehouse_price)
    if not is_profitable(
        warehouse_condition=snapshot.warehouse_condition,
        analysis=analysis,
        min_profit=settings.min_profit,
        min_discount=settings.min_discount,
        require_condition_match=settings.require_condition_match,
        allowed_conditions=settings.allowed_conditions,
    ):
        _log(
            f"[Filter] Skipped {snapshot.title} | profit={analysis.profit:.2f} TL, "
            f"discount={analysis.discount_percent:.1f}% | "
            f"condition={snapshot.warehouse_condition or 'unknown'} | "
            f"rules=min_profit:{settings.min_profit},min_discount:{settings.min_discount},"
            f"require_condition_match:{settings.require_condition_match}"
        )
        return

    verify_snapshot: ProductSnapshot | None = None
    if settings.verify_enabled:
        await asyncio.sleep(settings.verify_delay_seconds)
        try:
            verify_snapshot = await scraper.fetch_product(name, url)
        except Exception as exc:
            _log(f"[Verify] Failed second check for {name}: {exc}")
            return

        if not _is_snapshot_consistent(
            first=snapshot,
            second=verify_snapshot,
            tolerance_percent=settings.verify_price_tolerance_percent,
        ):
            _log(f"[Verify] Inconsistent data, skipped alert for {snapshot.title}")
            return

        # Use the second snapshot for final notification payload.
        snapshot = verify_snapshot
        analysis = analyze_prices(snapshot.normal_price, snapshot.warehouse_price)

    confidence_score = _calculate_confidence_score(
        snapshot=snapshot,
        verify_snapshot=verify_snapshot if settings.verify_enabled else None,
        verify_enabled=settings.verify_enabled,
        tolerance_percent=settings.verify_price_tolerance_percent,
        profit=analysis.profit,
        discount_percent=analysis.discount_percent,
    )
    if confidence_score < settings.min_confidence_score:
        _log(
            f"[Confidence] Skipped {snapshot.title} | "
            f"score={confidence_score}/100 < min={settings.min_confidence_score}"
        )
        return

    msg = format_alert(snapshot, analysis.profit, analysis.discount_percent, confidence_score)
    now_ts = time.time()
    key = _alert_key(snapshot)
    if not _should_send_alert(
        key=key,
        sent_cache=sent_cache,
        now_ts=now_ts,
        cooldown_seconds=settings.alert_cooldown_seconds,
    ):
        _log(f"[Notifier] Cooldown active, skipped duplicate alert for {snapshot.title}")
        return

    clean_url = _clean_product_url(snapshot.url)
    sent = await notifier.send(
        msg,
        button_url=clean_url,
        button_text="Open on Amazon",
        image_url=snapshot.image_url,
    )
    if sent:
        sent_cache[key] = now_ts
        _log(f"[Notifier] Alert sent for {snapshot.title}")


async def run_loop() -> None:
    settings = load_settings()
    products = load_products(settings.products_file)

    if not products:
        _log("No valid products found in products file.")
        return

    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
    scraper = AmazonScraper(user_agent=settings.user_agent)

    await scraper.start()
    _log(f"Bot started. Tracking {len(products)} products...")
    startup_sent = await notifier.send(format_startup_message(product_count=len(products), settings=settings))
    if startup_sent:
        _log("[Notifier] Startup message sent.")

    semaphore = asyncio.Semaphore(settings.max_concurrency)
    sent_alert_cache = load_alert_cache(settings.alert_cache_file)

    try:
        while True:
            async def guarded(product: dict[str, str]) -> None:
                async with semaphore:
                    await process_product(
                        scraper=scraper,
                        notifier=notifier,
                        product=product,
                        settings=settings,
                        sent_cache=sent_alert_cache,
                    )
                    await asyncio.sleep(random.uniform(1.0, 2.5))

            await asyncio.gather(*(guarded(product) for product in products))
            _prune_cache(
                sent_cache=sent_alert_cache,
                now_ts=time.time(),
                cooldown_seconds=settings.alert_cooldown_seconds,
            )
            save_alert_cache(settings.alert_cache_file, sent_alert_cache)
            await asyncio.sleep(settings.check_interval)
    finally:
        shutdown_sent = await notifier.send(format_shutdown_message(product_count=len(products)))
        if shutdown_sent:
            _log("[Notifier] Shutdown message sent.")
        save_alert_cache(settings.alert_cache_file, sent_alert_cache)
        await scraper.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_loop())
    except KeyboardInterrupt:
        _log("Bot stopped.")
