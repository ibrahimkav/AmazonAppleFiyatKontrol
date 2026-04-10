from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright


MONEY_RE = re.compile(r"([\d\.,]+)")
CONDITION_RE = re.compile(r"(like new|very good|good|acceptable)", re.IGNORECASE)
ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})", re.IGNORECASE)


@dataclass(frozen=True)
class ProductSnapshot:
    title: str
    url: str
    warehouse_price: Optional[float]
    normal_price: Optional[float]
    warehouse_condition: Optional[str]
    image_url: Optional[str]
    normal_price_source_count: int
    warehouse_price_source_count: int
    condition_confidence: int


def _parse_price(text: str | None) -> Optional[float]:
    if not text:
        return None

    match = MONEY_RE.search(text.replace("TL", "").replace("TRY", ""))
    if not match:
        return None

    raw = match.group(1).strip()
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")

    try:
        return float(raw)
    except ValueError:
        return None


def _select_first_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(strip=True)
            if text:
                return text
    return None


def _collect_price_candidates(soup: BeautifulSoup, selectors: list[str]) -> list[float]:
    prices: list[float] = []
    for selector in selectors:
        for node in soup.select(selector):
            text = node.get_text(strip=True)
            parsed = _parse_price(text)
            if parsed is not None:
                prices.append(parsed)
    return prices


def _extract_price_from_text_candidates(candidates: list[str]) -> Optional[float]:
    for text in candidates:
        parsed = _parse_price(text)
        if parsed is not None:
            return parsed
    return None


def _extract_prices_from_scripts(html: str) -> list[float]:
    # Amazon embeds price-like values in scripts/JSON blocks; this is a fallback.
    raw_values = re.findall(r'"(?:price|priceAmount|amount|value)"\s*:\s*"([\d\.,]+)"', html, flags=re.IGNORECASE)
    prices: list[float] = []
    for raw in raw_values:
        parsed = _parse_price(raw)
        if parsed is not None:
            prices.append(parsed)
    return prices


class AmazonScraper:
    def __init__(self, *, user_agent: str) -> None:
        self.user_agent = user_agent
        self._playwright = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _new_page(self) -> Page:
        if not self._browser:
            raise RuntimeError("Scraper not started. Call start() first.")
        context = await self._browser.new_context(user_agent=self.user_agent)
        return await context.new_page()

    async def fetch_product(self, name: str, url: str) -> ProductSnapshot:
        page = await self._new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            html = await page.content()
            snapshot = self._extract(name=name, url=url, html=html)

            # If this is a search page (or extraction fails), try first product detail page.
            if (snapshot.normal_price is None or snapshot.warehouse_price is None) and "/s?" in url:
                detail_url = self._extract_first_product_url_from_search(html, base_url="https://www.amazon.com.tr")
                if detail_url:
                    await page.goto(detail_url, wait_until="domcontentloaded", timeout=45_000)
                    detail_html = await page.content()
                    snapshot = self._extract(name=name, url=detail_url, html=detail_html)

            # If still missing prices on product page, try offer-listing fallback by ASIN.
            if snapshot.normal_price is None or snapshot.warehouse_price is None:
                asin = self._extract_asin(snapshot.url)
                if asin:
                    offer_url = f"https://www.amazon.com.tr/gp/offer-listing/{asin}"
                    await page.goto(offer_url, wait_until="domcontentloaded", timeout=45_000)
                    offer_html = await page.content()
                    offer_snapshot = self._extract(name=name, url=offer_url, html=offer_html)
                    snapshot = self._merge_snapshots(primary=snapshot, fallback=offer_snapshot)

            return snapshot
        finally:
            await page.context.close()

    def _extract_asin(self, url: str) -> str | None:
        match = ASIN_RE.search(url)
        return match.group(1).upper() if match else None

    def _merge_snapshots(self, *, primary: ProductSnapshot, fallback: ProductSnapshot) -> ProductSnapshot:
        return ProductSnapshot(
            title=primary.title or fallback.title,
            url=primary.url,
            warehouse_price=primary.warehouse_price if primary.warehouse_price is not None else fallback.warehouse_price,
            normal_price=primary.normal_price if primary.normal_price is not None else fallback.normal_price,
            warehouse_condition=primary.warehouse_condition or fallback.warehouse_condition,
            image_url=primary.image_url or fallback.image_url,
            normal_price_source_count=primary.normal_price_source_count + fallback.normal_price_source_count,
            warehouse_price_source_count=primary.warehouse_price_source_count + fallback.warehouse_price_source_count,
            condition_confidence=max(primary.condition_confidence, fallback.condition_confidence),
        )

    def _extract_first_product_url_from_search(self, html: str, base_url: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.select("a.a-link-normal.s-no-outline"):
            href = link.get("href")
            if not href:
                continue
            if "/dp/" in href:
                if href.startswith("http://") or href.startswith("https://"):
                    return href
                return f"{base_url}{href}"
        return None

    def _extract(self, *, name: str, url: str, html: str) -> ProductSnapshot:
        soup = BeautifulSoup(html, "html.parser")

        title = name
        title_node = soup.select_one("#productTitle")
        if title_node and title_node.get_text(strip=True):
            title = title_node.get_text(strip=True)

        image_url = None
        image_node = soup.select_one("#landingImage")
        if image_node and image_node.get("src"):
            image_url = str(image_node.get("src"))
        if not image_url:
            og_image = soup.select_one("meta[property='og:image']")
            if og_image and og_image.get("content"):
                image_url = str(og_image.get("content"))

        normal_price_selectors = [
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            "#corePrice_feature_div .a-price .a-offscreen",
            "#apex_desktop .a-price .a-offscreen",
            "#price_inside_buybox",
            ".a-price.a-text-price .a-offscreen",
            ".a-text-price .a-offscreen",
            ".a-price.aok-align-center .a-offscreen",
            ".a-price .a-offscreen",
        ]
        normal_price_candidates = _collect_price_candidates(soup, normal_price_selectors)
        if not normal_price_candidates:
            normal_price_candidates = _extract_prices_from_scripts(html)
        normal_price_source_count = len(normal_price_candidates)
        normal_price = normal_price_candidates[0] if normal_price_candidates else None

        warehouse_price = None
        warehouse_condition = None
        warehouse_price_source_count = 0
        condition_confidence = 0

        warehouse_price_selectors = [
            "#aod-price-0 .a-price .a-offscreen",
            "#aod-offer .a-price .a-offscreen",
            ".aod-information-block .a-price .a-offscreen",
            "#usedBuyBox .a-price .a-offscreen",
            "#buyBoxUsed .a-price .a-offscreen",
            "#buyBox .a-price .a-offscreen",
            "#corePrice_feature_div .a-price .a-offscreen",
        ]
        warehouse_price_candidates = _collect_price_candidates(soup, warehouse_price_selectors)
        if not warehouse_price_candidates:
            warehouse_price_candidates = _extract_prices_from_scripts(html)
        warehouse_price_source_count = len(warehouse_price_candidates)
        if warehouse_price_candidates:
            # Heuristic: cheapest candidate is typically used/warehouse offer.
            warehouse_price = min(warehouse_price_candidates)
            if normal_price is None:
                normal_price = max(warehouse_price_candidates)
                normal_price_source_count = len(warehouse_price_candidates)

        warehouse_condition = _select_first_text(
            soup,
            [
                "#aod-offer-heading h5",
                "#aod-offer-heading .a-size-base",
                "#usedBuySection .a-color-base",
                "#buyBoxUsedCondition .a-color-base",
                "#usedItemCondition",
                "#condition",
            ],
        )
        if warehouse_condition:
            condition_confidence = 80

        # Fallback selectors because Amazon DOM frequently changes.
        if warehouse_price is None:
            fallback = soup.find(string=re.compile(r"(Warehouse|Used|Renewed)", re.IGNORECASE))
            if fallback:
                parent_text = fallback.parent.get_text(" ", strip=True) if fallback.parent else ""
                warehouse_price = _parse_price(parent_text)
                if warehouse_price is None and fallback.parent:
                    candidate_texts = [el.get_text(" ", strip=True) for el in fallback.parent.find_all(["span", "div", "p"])]
                    warehouse_price = _extract_price_from_text_candidates(candidate_texts)
                if warehouse_price is not None:
                    warehouse_price_source_count += 1

        if warehouse_condition is None:
            condition_hint = soup.find(string=CONDITION_RE)
            if condition_hint:
                warehouse_condition = condition_hint.strip()
                condition_confidence = 55

        return ProductSnapshot(
            title=title,
            url=url,
            warehouse_price=warehouse_price,
            normal_price=normal_price,
            warehouse_condition=warehouse_condition,
            image_url=image_url,
            normal_price_source_count=normal_price_source_count,
            warehouse_price_source_count=warehouse_price_source_count,
            condition_confidence=condition_confidence,
        )
