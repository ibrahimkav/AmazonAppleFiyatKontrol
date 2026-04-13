from __future__ import annotations

import aiohttp
import json
import sys


def _log(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        safe = message.encode("cp1254", errors="replace").decode("cp1254", errors="replace")
        sys.stdout.write(f"{safe}\n")
        sys.stdout.flush()


def _telegram_error_hint(status: int, body: str) -> str:
    try:
        data = json.loads(body)
        desc = (data.get("description") or "").lower()
    except json.JSONDecodeError:
        desc = body.lower()
    parts: list[str] = []
    if status == 401 or "unauthorized" in desc:
        parts.append(
            "TELEGRAM_BOT_TOKEN geçersiz veya iptal; BotFather’dan yeni token alın."
        )
    if "chat not found" in desc or "chat_id is empty" in desc:
        parts.append(
            "Önce botunuza /start gönderin; TELEGRAM_CHAT_ID yanlış olabilir. "
            "py -3 scripts/find_telegram_chat.py ile doğru id’yi görün."
        )
    if not parts:
        return ""
    return " | ipucu: " + " ".join(parts)


class TelegramNotifier:
    def __init__(self, *, bot_token: str, chat_ids: tuple[str, ...]) -> None:
        self.bot_token = bot_token.strip()
        self.chat_ids = tuple(chat_id.strip() for chat_id in chat_ids if chat_id.strip())

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_ids)

    async def send(
        self,
        message: str,
        *,
        button_url: str | None = None,
        button_text: str = "Open Product",
        image_url: str | None = None,
        parse_mode: str | None = "HTML",
    ) -> bool:
        if not self.enabled:
            _log("[Notifier] Telegram credentials missing, message skipped.")
            return False

        reply_markup = None
        if button_url:
            reply_markup = {"inline_keyboard": [[{"text": button_text, "url": button_url}]]}

        sent_count = 0
        try:
            async with aiohttp.ClientSession() as session:
                for chat_id in self.chat_ids:
                    if image_url:
                        photo_url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
                        photo_payload = {
                            "chat_id": chat_id,
                            "photo": image_url,
                            "caption": message[:1024],
                        }
                        if parse_mode:
                            photo_payload["parse_mode"] = parse_mode
                        if reply_markup is not None:
                            photo_payload["reply_markup"] = reply_markup
                        async with session.post(photo_url, json=photo_payload, timeout=15) as response:
                            if response.status == 200:
                                sent_count += 1
                                continue
                            body = await response.text()
                            _log(
                                f"[Notifier] sendPhoto failed chat={chat_id} {response.status}: {body}"
                                f"{_telegram_error_hint(response.status, body)}"
                            )

                    message_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                    message_payload = {
                        "chat_id": chat_id,
                        "text": message,
                        "disable_web_page_preview": False,
                    }
                    if parse_mode:
                        message_payload["parse_mode"] = parse_mode
                    if reply_markup is not None:
                        message_payload["reply_markup"] = reply_markup
                    async with session.post(message_url, json=message_payload, timeout=15) as response:
                        if response.status != 200:
                            body = await response.text()
                            parse_issue = (
                                response.status == 400
                                and parse_mode == "HTML"
                                and ("parse" in body.lower() or "entities" in body.lower())
                            )
                            if parse_issue:
                                _log(
                                    f"[Notifier] HTML parse hatası (chat={chat_id}), düz metinle yeniden deneniyor…"
                                )
                                fallback_payload = {
                                    "chat_id": chat_id,
                                    "text": message,
                                    "disable_web_page_preview": False,
                                }
                                if reply_markup is not None:
                                    fallback_payload["reply_markup"] = reply_markup
                                async with session.post(
                                    message_url, json=fallback_payload, timeout=15
                                ) as fallback_response:
                                    if fallback_response.status == 200:
                                        sent_count += 1
                                    else:
                                        fallback_body = await fallback_response.text()
                                        _log(
                                            f"[Notifier] Telegram fallback error chat={chat_id} "
                                            f"{fallback_response.status}: {fallback_body}"
                                            f"{_telegram_error_hint(fallback_response.status, fallback_body)}"
                                        )
                                continue
                            _log(
                                f"[Notifier] Telegram error chat={chat_id} {response.status}: {body}"
                                f"{_telegram_error_hint(response.status, body)}"
                            )
                            continue
                        sent_count += 1
            return sent_count > 0
        except Exception as exc:
            _log(f"[Notifier] Telegram request failed: {exc}")
            return False
