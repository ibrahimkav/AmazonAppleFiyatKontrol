"""
Doğru TELEGRAM_CHAT_ID için:
1) Telegram'da kendi botunuza gidin ve /start yazın.
2) Proje kökünden: py -3 scripts/find_telegram_chat.py
3) Çıkan chat_id değerini .env içinde TELEGRAM_CHAT_ID=... olarak kaydedin.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN .env içinde yok.")
        sys.exit(1)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")
        sys.exit(1)
    except OSError as e:
        print(f"Bağlantı hatası: {e}")
        sys.exit(1)

    data = json.loads(raw)
    if not data.get("ok"):
        print(data)
        sys.exit(1)

    updates = data.get("result") or []
    if not updates:
        print(
            "Henüz mesaj yok. Telegram’da botunuza gidip /start gönderin, "
            "ardından bu scripti tekrar çalıştırın."
        )
        sys.exit(0)

    seen: set[int] = set()
    for u in updates:
        chat = None
        if "message" in u:
            chat = u["message"].get("chat")
        elif "edited_message" in u:
            chat = u["edited_message"].get("chat")
        elif "callback_query" in u:
            chat = (u.get("callback_query") or {}).get("message", {}).get("chat")
        if not chat:
            continue
        cid = chat.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        print(f"TELEGRAM_CHAT_ID={cid}  (type={chat.get('type')}  {title})")

    print("\nYukarıdaki sayıyı .env dosyasına TELEGRAM_CHAT_ID olarak yazın.")


if __name__ == "__main__":
    main()
