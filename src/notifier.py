from __future__ import annotations

import aiohttp
import sys


def _log(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        safe = message.encode("cp1254", errors="replace").decode("cp1254", errors="replace")
        sys.stdout.write(f"{safe}\n")
        sys.stdout.flush()


class TelegramNotifier:
    def __init__(self, *, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(
        self,
        message: str,
        *,
        button_url: str | None = None,
        button_text: str = "Open Product",
        image_url: str | None = None,
    ) -> bool:
        if not self.enabled:
            _log("[Notifier] Telegram credentials missing, message skipped.")
            return False

        reply_markup = None
        if button_url:
            reply_markup = {"inline_keyboard": [[{"text": button_text, "url": button_url}]]}

        try:
            async with aiohttp.ClientSession() as session:
                if image_url:
                    photo_url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
                    photo_payload = {
                        "chat_id": self.chat_id,
                        "photo": image_url,
                        "caption": message[:1024],
                        "parse_mode": "HTML",
                    }
                    if reply_markup is not None:
                        photo_payload["reply_markup"] = reply_markup
                    async with session.post(photo_url, json=photo_payload, timeout=15) as response:
                        if response.status == 200:
                            return True
                        body = await response.text()
                        _log(f"[Notifier] sendPhoto failed {response.status}: {body}")

                message_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                message_payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                }
                if reply_markup is not None:
                    message_payload["reply_markup"] = reply_markup
                async with session.post(message_url, json=message_payload, timeout=15) as response:
                    if response.status != 200:
                        body = await response.text()
                        _log(f"[Notifier] Telegram error {response.status}: {body}")
                        return False
                    return True
        except Exception as exc:
            _log(f"[Notifier] Telegram request failed: {exc}")
            return False
