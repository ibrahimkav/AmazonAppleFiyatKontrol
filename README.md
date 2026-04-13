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

## Çalıştırma

Proje kökünden:

```bash
python src/main.py
```

## Railway Deploy

- `railway.json` içinde build adımı Playwright Chromium kurulumunu otomatik yapar.
- Başlatma komutu: `python src/main.py`
- Python sürümü `.python-version` ile `3.12` olarak sabitlenmiştir.

## Yapılandırma

- **Ürün başına** `alert_below_try` (TL): ödeme fiyatı bu değerin altına/altına inince uyarı.
- **Ürün başına** `alert_discount_below_normal_try` (TL): sayfadaki üst fiyat ile düşük fiyat arasındaki fark en az bu kadar TL ise uyarı (ör. iPhone için 10000).
- Ürün linklerini mümkünse `https://www.amazon.com.tr/dp/ASIN` biçiminde girin.

Varsayılanlar `.env` içinde `DEFAULT_ALERT_BELOW_TRY` / `DEFAULT_ALERT_DISCOUNT_TRY` ile verilebilir.

Amazon HTML’i değişebilir; fiyat okunamazsa `src/scraper.py` seçicileri güncellenmelidir.

## Notlar

- Bot ürün sayfasındaki kardeş varyantları (renk/depolama) da sınırlı sayıda tarar ve fiyatı buna göre birleştirir.
- Aksesuar başlıkları (kılıf, ekran koruyucu vb.) ve iPhone için gerçek dışı düşük fiyatlar filtrelenir.
