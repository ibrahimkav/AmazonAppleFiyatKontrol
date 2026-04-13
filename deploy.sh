#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "📦 Python ve sanal ortam..."
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
python3 -m venv venv
source venv/bin/activate

echo "📚 Bağımlılıklar..."
pip install -r requirements.txt
playwright install chromium

echo ""
echo "✅ Kurulum bitti. .env dosyasını doldurun (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ...)."
echo "Çalıştırma:"
echo "  source venv/bin/activate && python src/main.py"
echo "Arka plan:"
echo "  nohup python src/main.py >> bot.log 2>&1 &"
