"""
Amazon TR fiyat tahmini Groq (LLM) ile. Canlı web araması yok; istenirse ürün URL’sinden
hafif HTTP ile HTML parçası alınıp modele verilir (Playwright gerekmez).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from groq_client import groq_chat_completion
from scraper import ProductSnapshot, snapshot_from_html


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSON bulunamadı")
    return json.loads(text[start : end + 1])


# Groq ücretsiz katmanda tek istekte ~12k token üst sınırı var; tüm HTML göndermek 40k+ token üretir.
_MAX_HTML_FOR_PROMPT = 6_000


def _shrink_html_for_llm(html: str, max_chars: int = _MAX_HTML_FOR_PROMPT) -> str:
    """Script/style gürültüsünü at, boşlukları sıkıştır, Groq token sınırına uy."""
    if not html:
        return ""
    h = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    h = re.sub(r"<style[\s\S]*?</style>", " ", h, flags=re.IGNORECASE)
    h = re.sub(r"\s+", " ", h)
    return h[:max_chars].strip()


def _fetch_page_snippet(url: str, user_agent: str, max_bytes: int = 512_000) -> str:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(max_bytes)
        return raw.decode("utf-8", errors="replace")
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return ""


def _parse_tr_price_string(raw: str) -> float | None:
    """Türkçe biçim: 45.999,00 veya 45.999 → float."""
    s = raw.strip().replace(" ", "")
    if not s:
        return None
    try:
        if "," in s and re.search(r",\d{1,2}$", s):
            left, right = s.rsplit(",", 1)
            left = left.replace(".", "")
            return float(f"{left}.{right}")
        if "." in s and "," not in s:
            parts = s.split(".")
            if len(parts) >= 2 and all(p.isdigit() for p in parts):
                if len(parts[-1]) == 3:
                    return float("".join(parts))
        return float(s.replace(".", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _extract_try_amounts_from_html(html: str) -> list[float]:
    """HTML içindeki TL tutar adayları (tekrarlar elenir)."""
    if not html:
        return []
    candidates: list[float] = []
    # 12.345,67 veya 123,45
    for m in re.finditer(
        r"(?<![\d])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d])",
        html,
    ):
        v = _parse_tr_price_string(m.group(1))
        if v is not None and 49 <= v <= 3_000_000:
            candidates.append(v)
    # 12.345 (binlik nokta, kuruş yok)
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:\.\d{3})+)(?![\d,])", html):
        v = _parse_tr_price_string(m.group(1))
        if v is not None and 49 <= v <= 3_000_000:
            candidates.append(v)
    return candidates


def _best_and_list_from_amounts(amounts: list[float]) -> tuple[float | None, float | None]:
    if not amounts:
        return None, None
    u = sorted(set(amounts))
    if len(u) == 1:
        return u[0], None
    return u[0], u[-1]


def _float_or_none(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _numeric_should_alert(
    best: float | None,
    lst: float | None,
    *,
    use_discount_rule: bool,
    use_abs_rule: bool,
    abs_try: float | None,
    discount_try: float | None,
) -> bool:
    if best is None:
        return False
    if use_abs_rule and abs_try is not None and best <= abs_try:
        return True
    if (
        use_discount_rule
        and discount_try is not None
        and lst is not None
        and lst - best >= float(discount_try) - 1e-6
    ):
        return True
    return False


def _fast_response_if_html_has_prices(
    page_snap: ProductSnapshot,
    *,
    product_name: str,
    product_url: str,
    use_discount_rule: bool,
    use_abs_rule: bool,
    abs_try: float | None,
    discount_try: float | None,
) -> dict[str, Any] | None:
    """HTML’den en az bir fiyat çıktıysa Groq çağrılmaz (TPM/bekleme yok)."""
    pay, lst = _pay_list_from_snapshot(page_snap)
    if pay is None:
        return None
    should = _numeric_should_alert(
        pay,
        lst,
        use_discount_rule=use_discount_rule,
        use_abs_rule=use_abs_rule,
        abs_try=abs_try,
        discount_try=discount_try,
    )
    title = (page_snap.title or "").strip() or product_name
    return {
        "should_alert": should,
        "product_title": title[:240],
        "best_price_try": pay,
        "list_price_try": lst,
        "amazon_url": product_url,
        "notes_tr": "Fiyat sayfa HTML’inden okundu; Groq atlandı (hızlı).",
    }


def _pay_list_from_snapshot(snap: ProductSnapshot) -> tuple[float | None, float | None]:
    """Playwright ile aynı ProductSnapshot anlamı: ödeme düşük, liste yüksek."""
    w, n = snap.warehouse_price, snap.normal_price
    if w is None and n is None:
        return None, None
    if w is not None and n is not None:
        lo, hi = min(w, n), max(w, n)
        if hi - lo < 0.02:
            return lo, None
        return lo, hi
    x = w if w is not None else n
    return x, None


def _merge_html_prices_into_data(
    data: dict[str, Any],
    html_snip: str,
    snapshot: ProductSnapshot | None,
    *,
    use_discount_rule: bool,
    use_abs_rule: bool,
    abs_try: float | None,
    discount_try: float | None,
) -> None:
    """Önce BeautifulSoup (scraper seçicileri), yoksa regex; Groq null bıraktıysa doldur."""
    pay_dom, list_dom = _pay_list_from_snapshot(snapshot) if snapshot is not None else (None, None)

    amounts = _extract_try_amounts_from_html(html_snip)
    hb, hl = _best_and_list_from_amounts(amounts)

    best_src = pay_dom if pay_dom is not None else hb
    list_src = list_dom if list_dom is not None else hl

    if _float_or_none(data.get("best_price_try")) is None and best_src is not None:
        data["best_price_try"] = best_src
    if _float_or_none(data.get("list_price_try")) is None and list_src is not None:
        data["list_price_try"] = list_src

    tit = str(data.get("product_title") or "").strip()
    if snapshot is not None and snapshot.title and tit in {"", "Ürün"}:
        data["product_title"] = snapshot.title[:240]

    best = _float_or_none(data.get("best_price_try"))
    lst = _float_or_none(data.get("list_price_try"))
    llm_alert = bool(data.get("should_alert"))

    numeric_alert = _numeric_should_alert(
        best,
        lst,
        use_discount_rule=use_discount_rule,
        use_abs_rule=use_abs_rule,
        abs_try=abs_try,
        discount_try=discount_try,
    )

    data["should_alert"] = llm_alert or numeric_alert
    prev_notes = str(data.get("notes_tr") or "").strip()
    if not prev_notes:
        if pay_dom is not None or list_dom is not None:
            data["notes_tr"] = "Fiyatlar sayfa HTML’inden (Amazon seçicileri, Playwright ile aynı mantık) okundu."
        elif amounts:
            data["notes_tr"] = f"Fiyat adayları metin taramasıyla bulundu ({len(amounts)} eşleşme)."


def evaluate_amazon_tr_with_groq(
    *,
    api_key: str,
    model: str,
    user_agent: str,
    product_name: str,
    product_url: str,
    use_discount_rule: bool,
    use_abs_rule: bool,
    abs_try: float | None,
    discount_try: float | None,
) -> tuple[dict[str, Any] | None, bool]:
    """
    (data, groq_api_called). groq_api_called False ise HTML hızlı yolu (TPM beklemesi gerekmez).
    """
    rules: list[str] = []
    if use_discount_rule and discount_try is not None:
        rules.append(
            f"İndirim kuralı: Liste üst fiyatı ile ödenecek en düşük tutar arasındaki fark "
            f"en az {discount_try:.0f} TL ise should_alert true olmalı."
        )
    if use_abs_rule and abs_try is not None:
        rules.append(
            f"Sabit eşik: Tahmini en düşük peşin satış fiyatı {abs_try:.0f} TL veya altıysa should_alert true olmalı."
        )
    rules_txt = "\n".join(f"- {r}" for r in rules) if rules else "- Kural yok (bu olmamalı)."

    html_snip = _fetch_page_snippet(product_url, user_agent)
    page_snap: ProductSnapshot | None = None
    if html_snip:
        page_snap = snapshot_from_html(name=product_name, url=product_url, html=html_snip)
        fast = _fast_response_if_html_has_prices(
            page_snap,
            product_name=product_name,
            product_url=product_url,
            use_discount_rule=use_discount_rule,
            use_abs_rule=use_abs_rule,
            abs_try=abs_try,
            discount_try=discount_try,
        )
        if fast is not None:
            return fast, False

    html_block = ""
    if html_snip:
        cut = _shrink_html_for_llm(html_snip)
        html_block = (
            f"\n\nAşağıda ürün sayfasından alınan HTML özeti var (kısaltıldı; fiyatları buradan çıkarmaya çalış):\n"
            f"<<<HTML>>>\n{cut}\n<<<HTML SON>>>\n"
        )
    else:
        html_block = (
            "\n\nSayfa HTML’i alınamadı (bot engeli vb.). Genel bilgine dayan; "
            "emin değilsen should_alert false yap.\n"
        )

    prompt = f"""Amazon Türkiye (amazon.com.tr) için bu ürünün güncel fiyatını mümkün olduğunca doğru tahmin et.

Ürün: {product_name}
Ürün URL: {product_url}
{html_block}

Kurallar:
{rules_txt}

Görev:
1) HTML’de net fiyat görüyorsan TL cinsinden kullan; görmüyorsan dürüstçe belirt.
2) best_price_try = ana satın alma / sepet fiyatı (en düşük net).
3) list_price_try = üstü çizili / liste fiyatı (yoksa null).

Yanıtın SADECE tek bir JSON nesnesi olsun (markdown veya açıklama yok):
{{
  "should_alert": true veya false,
  "product_title": "kısa ürün başlığı",
  "best_price_try": sayı veya null,
  "list_price_try": sayı veya null,
  "amazon_url": "https://... tam URL veya boş string",
  "notes_tr": "Türkçe 1-3 cümle: veri kaynağın, belirsizlik"
}}

Emin değilsen should_alert false yap."""

    raw = groq_chat_completion(
        api_key=api_key,
        model=model,
        user_message=prompt,
        max_tokens=2048,
        temperature=0.2,
    )
    if not raw:
        return None, True
    data = _extract_json_object(raw)
    if html_snip:
        _merge_html_prices_into_data(
            data,
            html_snip,
            page_snap,
            use_discount_rule=use_discount_rule,
            use_abs_rule=use_abs_rule,
            abs_try=abs_try,
            discount_try=discount_try,
        )
    return data, True


def snapshot_from_groq_dict(product_fallback_url: str, data: dict[str, Any]) -> ProductSnapshot:
    url = (data.get("amazon_url") or "").strip() or product_fallback_url
    best = data.get("best_price_try")
    lst = data.get("list_price_try")
    title = str(data.get("product_title") or "Ürün")

    def _num(x: Any) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    return ProductSnapshot(
        title=title,
        url=url,
        warehouse_price=_num(best),
        normal_price=_num(lst),
        warehouse_condition=None,
        image_url=None,
        normal_price_source_count=1,
        warehouse_price_source_count=1,
        condition_confidence=0,
    )


def telegram_body_from_groq(data: dict[str, Any], *, use_discount: bool) -> str:
    import html as html_module

    _ = use_discount
    title = html_module.escape(str(data.get("product_title") or "Ürün"))
    notes = html_module.escape(str(data.get("notes_tr") or "").strip())
    best = data.get("best_price_try")
    lst = data.get("list_price_try")
    url = (data.get("amazon_url") or "").strip()

    lines = [
        "🔔 <b>Groq (LLM) — Amazon TR</b>\n",
        f"📱 <b>{title}</b>\n",
    ]
    if best is not None:
        lines.append(f"💰 Tahmini en düşük: <b>{float(best):,.2f} TL</b>")
    if lst is not None:
        lines.append(f"🏷️ Liste / üst: <b>{float(lst):,.2f} TL</b>")
    if notes:
        lines.append(f"\n💡 <i>{notes}</i>")
    if url:
        lines.append(f"\n🔗 {html_module.escape(url)}")
    lines.append("\n\n<i>Tahmindir; satın almadan Amazon’da doğrulayın.</i>")
    return "\n".join(lines)
