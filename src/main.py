from __future__ import annotations

import asyncio
import html
import json
import os
import random
import re
import sys
import time
from functools import partial
from pathlib import Path
from typing import Any

from config import load_settings
from groq_extra import generate_alert_comment
from groq_price import (
    evaluate_amazon_tr_with_groq,
    snapshot_from_groq_dict,
    telegram_body_from_groq,
)
from notifier import TelegramNotifier
from scraper import AmazonScraper, ProductSnapshot

# CMD / log dosyasında satırların hemen görünmesi (alt süreçler için; ana iş -u ile çalıştırmak)
os.environ.setdefault("PYTHONUNBUFFERED", "1")


def _effective_best_price(snapshot: ProductSnapshot) -> float | None:
    prices: list[float] = []
    if snapshot.warehouse_price is not None:
        prices.append(snapshot.warehouse_price)
    if snapshot.normal_price is not None:
        prices.append(snapshot.normal_price)
    return min(prices) if prices else None


def _resolve_alert_threshold(
    product: dict[str, str | float | None],
    default_alert_below_try: float,
) -> float | None:
    raw = product.get("alert_below_try")
    if raw is not None and raw != "":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    if default_alert_below_try > 0:
        return default_alert_below_try
    return None


def _resolve_alert_discount(
    product: dict[str, Any],
    default_alert_discount_try: float,
) -> float | None:
    raw = product.get("alert_discount_below_normal_try")
    if raw is not None and raw != "":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    if default_alert_discount_try > 0:
        return default_alert_discount_try
    return None


def _discount_savings_try(snapshot: ProductSnapshot) -> float | None:
    if snapshot.normal_price is None or snapshot.warehouse_price is None:
        return None
    hi = max(snapshot.normal_price, snapshot.warehouse_price)
    lo = min(snapshot.normal_price, snapshot.warehouse_price)
    return hi - lo


def filter_tracked_products(products: list[dict[str, Any]], settings: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in products:
        t = _resolve_alert_threshold(p, settings.default_alert_below_try)
        d = _resolve_alert_discount(p, settings.default_alert_discount_try)
        if (t is not None and t > 0) or (d is not None and d > 0):
            out.append(p)
    return out


def _log(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        safe = message.encode("cp1254", errors="replace").decode("cp1254", errors="replace")
        sys.stdout.write(f"{safe}\n")
        sys.stdout.flush()


def _fmt_try(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):,.2f} TL"
    except (TypeError, ValueError):
        return "?"


def load_products(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Products file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    products: list[dict[str, Any]] = []
    for item in raw:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            continue
        entry: dict[str, Any] = {"name": name, "url": url}
        if "alert_below_try" in item and item["alert_below_try"] is not None:
            try:
                entry["alert_below_try"] = float(item["alert_below_try"])
            except (TypeError, ValueError):
                entry["alert_below_try"] = None
        if "alert_discount_below_normal_try" in item and item["alert_discount_below_normal_try"] is not None:
            try:
                entry["alert_discount_below_normal_try"] = float(item["alert_discount_below_normal_try"])
            except (TypeError, ValueError):
                entry["alert_discount_below_normal_try"] = None
        products.append(entry)
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


def format_startup_message(*, product_count: int, settings: Any) -> str:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    quiet = "kapalı (sessiz)" if settings.quiet_threshold_skips else "açık"
    return (
        "✅ Fiyat izleyici çalışıyor\n"
        "📲 Bu mesaj Telegram botunuz üzerinden gönderildi.\n\n"
        f"Başlangıç: {started_at}\n"
        f"İzlenen ürün: {product_count}\n"
        f"Tarama aralığı: {settings.check_interval} sn\n"
        f"Varsayılan sabit eşik (TL): {settings.default_alert_below_try:.0f}\n"
        f"Varsayılan liste altı indirim (TL): {settings.default_alert_discount_try:.0f}\n"
        f"Uygun değilken log: {quiet}\n"
        f"Uyarı bekleme: {settings.alert_cooldown_seconds} sn\n"
        f"Fiyat motoru: {'Groq (LLM)' if settings.use_groq_price_engine else 'Playwright (yerel tarayıcı)'}"
        + (
            f"\nGroq çağrı aralığı: {settings.groq_min_interval_seconds:.0f} sn"
            if settings.use_groq_price_engine
            else ""
        )
    )


def format_shutdown_message(*, product_count: int) -> str:
    stopped_at = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"🛑 İzleyici durdu\n\nBitiş: {stopped_at}\nÜrün: {product_count}"


def format_threshold_alert(
    snapshot: ProductSnapshot,
    *,
    threshold_try: float,
    best_price: float,
) -> str:
    safe_title = html.escape(snapshot.title)
    cond = snapshot.warehouse_condition or ""
    cond_line = f"\n📦 Durum: <b>{html.escape(cond)}</b>" if cond.strip() else ""
    return (
        "🔔 <b>Fiyat eşiğinin altında</b>\n\n"
        f"📱 <b>{safe_title}</b>{cond_line}\n\n"
        f"💰 En düşük fiyat: <b>{best_price:,.2f} TL</b>\n"
        f"🎯 Eşik: <b>{threshold_try:,.0f} TL</b> ve altı\n\n"
        "Bilgilendirme amaçlıdır; satın alma Amazon’da sizin tarafınızda."
    )


def format_discount_alert(
    snapshot: ProductSnapshot,
    *,
    min_discount_try: float,
    savings_try: float,
    list_try: float,
    pay_try: float,
) -> str:
    safe_title = html.escape(snapshot.title)
    cond = snapshot.warehouse_condition or ""
    cond_line = f"\n📦 Durum: <b>{html.escape(cond)}</b>" if cond.strip() else ""
    return (
        "🔔 <b>Liste altı indirim</b>\n\n"
        f"📱 <b>{safe_title}</b>{cond_line}\n\n"
        f"🏷️ Üst fiyat: <b>{list_try:,.2f} TL</b>\n"
        f"💰 Ödeme: <b>{pay_try:,.2f} TL</b>\n"
        f"📉 İndirim: <b>{savings_try:,.2f} TL</b> (min: {min_discount_try:,.0f} TL)\n\n"
        "Bilgilendirme amaçlıdır; satın alma Amazon’da sizin tarafınızda."
    )


def _threshold_alert_key(snapshot: ProductSnapshot, threshold_try: float) -> str:
    clean = _clean_product_url(snapshot.url)
    return f"threshold|{clean}|{threshold_try:.0f}"


def _discount_alert_key(snapshot: ProductSnapshot, min_discount_try: float) -> str:
    clean = _clean_product_url(snapshot.url)
    return f"discount|{clean}|{min_discount_try:.0f}"


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


def _missing_parts(snapshot: ProductSnapshot) -> str:
    missing: list[str] = []
    if snapshot.normal_price is None:
        missing.append("normal_price")
    if snapshot.warehouse_price is None:
        missing.append("warehouse_price")
    return ", ".join(missing) if missing else "none"


async def enrich_alert_message(
    settings: Any,
    msg: str,
    product_name: str,
    detail_lines: list[str],
) -> str:
    if not settings.groq_api_key:
        return msg
    try:
        extra = await asyncio.to_thread(
            partial(
                generate_alert_comment,
                api_key=settings.groq_api_key,
                model=settings.groq_model,
                product_name=product_name,
                detail_lines=detail_lines,
            )
        )
        if not extra:
            return msg
        safe = html.escape(extra)
        return f"{msg}\n\n💡 <i>{safe}</i>"
    except Exception as exc:
        _log(f"[Groq] Özet atlanıyor: {exc}")
        return msg


async def process_product_threshold(
    *,
    scraper: AmazonScraper | None,
    notifier: TelegramNotifier,
    product: dict[str, Any],
    settings: Any,
    sent_cache: dict[str, float],
) -> bool:
    """
    Groq modunda: True = sonraki ürün öncesi TPM beklemesi uygula (Groq API çağrıldı).
    Playwright / diğer: her zaman False (dönüş yalnızca Groq döngüsünde kullanılır).
    """
    name = str(product["name"])
    url = str(product["url"])
    discount_try = _resolve_alert_discount(product, settings.default_alert_discount_try)
    abs_try = _resolve_alert_threshold(product, settings.default_alert_below_try)
    quiet = settings.quiet_threshold_skips

    use_discount = discount_try is not None and discount_try > 0
    use_abs = abs_try is not None and abs_try > 0
    if use_discount and use_abs:
        use_abs = False
    if not use_discount and not use_abs:
        return False

    if settings.use_groq_price_engine:
        if not settings.groq_api_key:
            _log(f"[Groq] {name}: GROQ_API_KEY yok, atlanıyor")
            return False
        try:
            data, groq_llm_used = await asyncio.to_thread(
                partial(
                    evaluate_amazon_tr_with_groq,
                    api_key=settings.groq_api_key,
                    model=settings.groq_model,
                    user_agent=settings.user_agent,
                    product_name=name,
                    product_url=url,
                    use_discount_rule=use_discount,
                    use_abs_rule=use_abs,
                    abs_try=abs_try,
                    discount_try=discount_try,
                )
            )
        except Exception as exc:
            _log(f"[Groq] {name}: {exc}")
            return True
        if not data:
            if not quiet:
                _log(f"[Groq] {name}: boş yanıt")
            return True
        if not groq_llm_used:
            _log(f"[Groq] {name}: hızlı yol (HTML’den fiyat, Groq API yok)")
        _log(
            f"[Groq] {name}: tahmini fiyat — net: {_fmt_try(data.get('best_price_try'))}, "
            f"liste: {_fmt_try(data.get('list_price_try'))}"
        )
        if not data.get("should_alert"):
            if not quiet:
                _log(f"[Groq] {name}: kural tutmadı veya emin değil")
            return groq_llm_used
        snapshot = snapshot_from_groq_dict(url, data)
        msg = telegram_body_from_groq(data, use_discount=use_discount)
        if use_discount:
            key = _discount_alert_key(snapshot, discount_try)
        else:
            key = _threshold_alert_key(snapshot, abs_try)
    else:
        if scraper is None:
            _log(f"[Scraper] {name}: Playwright kapalı — yapılandırma hatası")
            return False
        try:
            snapshot = await scraper.fetch_product(name, url)
        except Exception as exc:
            _log(f"[Scraper] Failed {name}: {exc}")
            return False

        _log(
            f"[Scraper] {name}: sayfa fiyatları — en düşük: {_fmt_try(_effective_best_price(snapshot))}, "
            f"liste/normal: {_fmt_try(snapshot.normal_price)}, ödeme/depo: {_fmt_try(snapshot.warehouse_price)}"
        )

        if use_discount:
            savings = _discount_savings_try(snapshot)
            if savings is None:
                if not quiet:
                    _log(f"[Threshold] {name}: iki fiyat yok, indirim hesaplanamıyor")
                return False
            list_ref = max(snapshot.normal_price or 0, snapshot.warehouse_price or 0)
            pay_ref = min(snapshot.normal_price or 0, snapshot.warehouse_price or 0)
            if savings < discount_try:
                if not quiet:
                    _log(
                        f"[Threshold] {name}: indirim {savings:.0f} TL < min {discount_try:.0f} TL "
                        f"(ust={list_ref:.0f} odeme={pay_ref:.0f})"
                    )
                return False
            msg = format_discount_alert(
                snapshot,
                min_discount_try=discount_try,
                savings_try=savings,
                list_try=list_ref,
                pay_try=pay_ref,
            )
            msg = await enrich_alert_message(
                settings,
                msg,
                name,
                [
                    f"Başlık: {snapshot.title}",
                    f"Üst fiyat: {list_ref:.0f} TL, ödeme: {pay_ref:.0f} TL",
                    f"İndirim: {savings:.0f} TL (eşik: en az {discount_try:.0f} TL)",
                ],
            )
            key = _discount_alert_key(snapshot, discount_try)
        else:
            best_price = _effective_best_price(snapshot)
            if best_price is None:
                _log(f"[Threshold] {name}: fiyat yok | eksik={_missing_parts(snapshot)}")
                return False
            if best_price > abs_try:
                if not quiet:
                    _log(f"[Threshold] {name}: {best_price:.0f} TL > esik {abs_try:.0f} TL")
                return False
            msg = format_threshold_alert(snapshot, threshold_try=abs_try, best_price=best_price)
            msg = await enrich_alert_message(
                settings,
                msg,
                name,
                [
                    f"Başlık: {snapshot.title}",
                    f"En düşük fiyat: {best_price:.0f} TL, eşik: {abs_try:.0f} TL ve altı",
                ],
            )
            key = _threshold_alert_key(snapshot, abs_try)

    now_ts = time.time()
    if not _should_send_alert(
        key=key,
        sent_cache=sent_cache,
        now_ts=now_ts,
        cooldown_seconds=settings.alert_cooldown_seconds,
    ):
        if not quiet:
            _log(f"[Notifier] Cooldown: {name}")
        return groq_llm_used if settings.use_groq_price_engine else False

    clean_url = _clean_product_url(snapshot.url)
    sent = await notifier.send(
        msg,
        button_url=clean_url,
        button_text="Amazon’da aç",
        image_url=snapshot.image_url,
    )
    if sent:
        sent_cache[key] = now_ts
        _log(f"[Notifier] Uyari: {name}")
    return groq_llm_used if settings.use_groq_price_engine else False


async def run_loop() -> None:
    settings = load_settings()
    raw_products = load_products(settings.products_file)
    products = filter_tracked_products(raw_products, settings)

    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_ids=settings.telegram_chat_ids,
    )

    if not products:
        _log(
            "İzlenecek ürün yok. data/products.json içinde alert_below_try veya "
            "alert_discount_below_normal_try ekleyin (veya .env DEFAULT_*)."
        )
        if notifier.enabled:
            empty_ok = await notifier.send(
                "⚠️ Amazon fiyat izleyici başladı ancak takip edilecek ürün yok.\n\n"
                "📲 Telegram bot bildirimi.\n\n"
                "Çözüm: products.json veya .env içinde DEFAULT_ALERT_BELOW_TRY / "
                "DEFAULT_ALERT_DISCOUNT_TRY değerlerinden en az biri > 0 olmalı.",
                parse_mode=None,
            )
            if empty_ok:
                _log("[Notifier] Boş liste uyarısı Telegram'a iletildi.")
            else:
                _log(
                    "[Notifier] Boş liste uyarısı gönderilemedi; TELEGRAM_BOT_TOKEN ve "
                    "TELEGRAM_CHAT_ID/TELEGRAM_CHAT_IDS kontrol edin (/start, scripts/find_telegram_chat.py)."
                )
        else:
            _log("[Notifier] Telegram anahtarı/sohbet id eksik; bildirim atlanıyor.")
        return

    scraper: AmazonScraper | None = None
    if not settings.use_groq_price_engine:
        scraper = AmazonScraper(user_agent=settings.user_agent)
        await scraper.start()
    _log(f"Bot started. Tracking {len(products)} products...")
    startup_sent = await notifier.send(
        format_startup_message(product_count=len(products), settings=settings),
        parse_mode=None,
    )
    if startup_sent:
        _log("[Notifier] Startup message sent (Telegram).")
    else:
        _log(
            "[Notifier] Başlangıç mesajı Telegram'a gitmedi; konsoldaki hata satırına bakın "
            "(token, chat_id(s), bot ile /start)."
        )

    sent_alert_cache = load_alert_cache(settings.alert_cache_file)

    async def run_one_product_check(product: dict[str, Any]) -> bool:
        name = str(product.get("name", "?"))
        t0 = time.time()
        _log(f"[Kontrol] → {name}")
        try:
            return await process_product_threshold(
                scraper=scraper,
                notifier=notifier,
                product=product,
                settings=settings,
                sent_cache=sent_alert_cache,
            )
        finally:
            _log(f"[Kontrol] ← {name} ({time.time() - t0:.1f} sn)")

    try:
        tur = 0
        while True:
            tur += 1
            n = len(products)
            _log(
                f"=== Tur #{tur} | {n} ürün | {time.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"tur sonrası bekleme {settings.check_interval} sn ==="
            )
            if settings.use_groq_price_engine:
                # Paralel istekler Groq ücretsiz TPM’i (dakikada ~12k token) anında doldurur; sırayla işle.
                for i, product in enumerate(products, start=1):
                    _log(f"[Sıra] {i}/{n} (TPM bekleme: en fazla {settings.groq_min_interval_seconds:.0f} sn, yalnız Groq API sonrası)")
                    need_tpm_sleep = await run_one_product_check(product)
                    if i < n and need_tpm_sleep:
                        _log(f"[Bekle] Groq TPM için {settings.groq_min_interval_seconds:.0f} sn…")
                        await asyncio.sleep(settings.groq_min_interval_seconds)
                    elif i < n and not need_tpm_sleep:
                        _log("[Bekle] atlandı (HTML hızlı yol — Groq çağrısı yok)")
            else:
                semaphore = asyncio.Semaphore(settings.max_concurrency)

                async def guarded(product: dict[str, Any]) -> None:
                    async with semaphore:
                        await run_one_product_check(product)
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
        shutdown_sent = await notifier.send(
            format_shutdown_message(product_count=len(products)),
            parse_mode=None,
        )
        if shutdown_sent:
            _log("[Notifier] Shutdown message sent (Telegram).")
        save_alert_cache(settings.alert_cache_file, sent_alert_cache)
        if scraper is not None:
            await scraper.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_loop())
    except KeyboardInterrupt:
        _log("Bot stopped.")
