# Amazon Warehouse Arbitrage Bot

Tracks Amazon Warehouse deals and sends Telegram alerts when items pass profit and discount filters.

## Features

- Async scraping with Playwright
- HTML parsing with BeautifulSoup
- Profit/discount analyzer
- Condition + threshold filtering
- Telegram real-time alerting

## Setup

1. Create venv and install dependencies:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install
```

2. Copy `.env.example` to `.env` and fill values:

```env
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id
CHECK_INTERVAL=30
MIN_PROFIT=300
MIN_DISCOUNT=25
ALERT_COOLDOWN_SECONDS=1800
ALERT_CACHE_FILE=data/alert_cache.json
VERIFY_ENABLED=true
VERIFY_DELAY_SECONDS=4
VERIFY_PRICE_TOLERANCE_PERCENT=7
MIN_CONFIDENCE_SCORE=70
```

3. Edit `data/products.json` with product URLs.

4. Run:

```bash
python src/main.py
```

## Notes

- Start with alert-only strategy (manual buying).
- Add randomized delays to reduce ban risk.
- Amazon page structure can change; selectors may need updates over time.
- Verification mode is enabled by default: profitable deals are re-scraped once before alerting.
- Confidence score filter is enabled: only deals above minimum confidence are alerted.
- Confidence uses multiple signals: spread, condition quality, and independent price source count.
