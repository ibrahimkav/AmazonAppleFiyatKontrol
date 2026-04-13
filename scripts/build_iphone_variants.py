from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

ACCESSORY_WORDS = (
    "kılıf",
    "kilif",
    "case",
    "cover",
    "ekran koruyucu",
    "tempered",
    "şarj",
    "sarj",
    "charger",
    "kablo",
    "lens protector",
    "kamera koruyucu",
)

MODEL_PHRASES = [
    "iphone 16 pro max",
    "iphone 16 pro",
    "iphone 16 plus",
    "iphone 16",
    "iphone 15 pro max",
    "iphone 15 pro",
    "iphone 15 plus",
    "iphone 15",
    "iphone 14 plus",
    "iphone 14",
    "iphone 13",
    "iphone 12",
    "iphone 11",
    "iphone se 3",
]


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read(900_000).decode("utf-8", errors="replace")


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def seems_accessory(title: str) -> bool:
    t = normalize(title)
    return any(w in t for w in ACCESSORY_WORDS)


def extract_asins(html: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for asin in re.findall(r"/dp/([A-Z0-9]{10})", html):
        a = asin.upper()
        if a in seen:
            continue
        seen.add(a)
        out.append(a)
    return out


def fetch_title_for_asin(asin: str) -> str:
    url = f"https://www.amazon.com.tr/dp/{asin}"
    html = fetch_html(url)
    m = re.search(r"<title>([\s\S]*?)</title>", html, flags=re.IGNORECASE)
    if not m:
        return ""
    return normalize(m.group(1))


def collect_variants_from_base(name: str, url: str, max_count: int = 5) -> list[dict[str, object]]:
    try:
        html = fetch_html(url)
    except Exception:
        return []
    asins = extract_asins(html)
    kept: list[dict[str, object]] = []
    model_phrase = normalize(name).replace("apple ", "").strip()
    model_key = model_phrase.replace(" ", "")
    for asin in asins:
        try:
            title_n = fetch_title_for_asin(asin)
        except Exception:
            continue
        if "iphone" not in title_n:
            continue
        if seems_accessory(title_n):
            continue
        if model_phrase not in title_n:
            continue
        other_model_hit = any(
            p in title_n and p != model_phrase for p in MODEL_PHRASES
        )
        if other_model_hit:
            continue
        if model_key[:7] not in title_n.replace(" ", "") and "iphone" in model_key:
            # Loose match: still allow if it clearly looks like a phone title.
            if "apple iphone" not in title_n:
                continue
        kept.append(
            {
                "name": f"{name} | {title_n[:80]}",
                "url": f"https://www.amazon.com.tr/dp/{asin}",
                "alert_discount_below_normal_try": 10000,
            }
        )
        if len(kept) >= max_count:
            break
    return kept


def main() -> None:
    products_path = Path(__file__).resolve().parents[1] / "data" / "products.json"
    base_products = json.loads(products_path.read_text(encoding="utf-8"))
    all_rows: list[dict[str, object]] = []
    for item in base_products:
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url:
            continue
        rows = collect_variants_from_base(name=name, url=url)
        if rows:
            all_rows.extend(rows)
        else:
            all_rows.append(
                {
                    "name": name,
                    "url": url,
                    "alert_discount_below_normal_try": 10000,
                }
            )
    # Deduplicate by URL while keeping first.
    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for row in all_rows:
        u = str(row["url"])
        if u in seen:
            continue
        seen.add(u)
        deduped.append(row)
    print(json.dumps(deduped, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
