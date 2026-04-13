# Amazon TR fiyat izleyici

`data/products.json` içindeki ürünleri periyodik tarar; **sabit fiyat eşiği** veya **liste altı indirim (TL)** kuralına uyunca Telegram’a uyarı gönderir.

## Kurulum

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

`.env.example` dosyasını `.env` olarak kopyalayıp doldurun. Sohbet kimliği için: `py scripts/find_telegram_chat.py`  
Birden fazla hedefe bildirim için `.env` içinde `TELEGRAM_CHAT_IDS=111,222` kullanabilirsiniz (tek hedef için `TELEGRAM_CHAT_ID` da desteklenir).

İsteğe bağlı **`GROQ_API_KEY`**: doluysa (Playwright uyarılarında) her uyarıya Groq ile 1–2 cümle Türkçe özet eklenir.

## Çalıştırma

Proje kökünden:

```bash
python src/main.py
```

## Yapılandırma

- **Ürün başına** `alert_below_try` (TL): ödeme fiyatı bu değerin altına/altına inince uyarı.
- **Ürün başına** `alert_discount_below_normal_try` (TL): sayfadaki üst fiyat ile düşük fiyat arasındaki fark en az bu kadar TL ise uyarı (ör. iPhone için 10000).

Varsayılanlar `.env` içinde `DEFAULT_ALERT_BELOW_TRY` / `DEFAULT_ALERT_DISCOUNT_TRY` ile verilebilir.

Amazon HTML’i değişebilir; fiyat okunamazsa `src/scraper.py` seçicileri güncellenmelidir.
