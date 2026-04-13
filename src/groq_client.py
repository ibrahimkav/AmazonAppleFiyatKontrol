"""Groq OpenAI-uyumlu chat/completions — ortak istek ve hata metni."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request


def _format_http_error(exc: urllib.error.HTTPError, body: str | None = None) -> str:
    raw = body if body is not None else exc.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        err = data.get("error") or {}
        msg = (err.get("message") or err.get("code") or raw)[:500]
    except (json.JSONDecodeError, TypeError):
        msg = raw[:300]

    line = f"Groq HTTP {exc.code}: {msg}"
    if exc.code == 403:
        line += (
            " | Olası nedenler: geçersiz/iptal anahtar, Groq bölge kısıtı; "
            "https://console.groq.com/keys adresinden anahtarı doğrulayın veya PRICE_ENGINE=playwright deneyin."
        )
    if exc.code == 413:
        line += " | İstek çok büyük: giriş metni token sınırını aşıyor (HTML kısaltılmalı)."
    return line


def _retry_after_seconds(http_body: str) -> float:
    m = re.search(r"try again in ([0-9.]+)s", http_body, re.IGNORECASE)
    if m:
        return min(float(m.group(1)) + 0.75, 90.0)
    return 15.0


def groq_chat_completion(
    *,
    api_key: str,
    model: str,
    user_message: str,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    timeout_sec: int = 90,
    max_retries: int = 4,
) -> str:
    """
    Başarılıysa asistan metni; Groq hata dönerse RuntimeError (açıklamalı).
    429 için yanıttaki bekleme süresine göre birkaç kez yeniden dener.
    """
    key = api_key.strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY boş")

    payload_bytes = json.dumps(
        {
            "model": model.strip(),
            "messages": [{"role": "user", "content": user_message}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")

    for attempt in range(max_retries):
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload_bytes,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AmazonBot/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choices = body.get("choices") or []
            if not choices:
                return ""
            msg = (choices[0].get("message") or {}).get("content")
            return (msg or "").strip()
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(_retry_after_seconds(raw))
                continue
            raise RuntimeError(_format_http_error(e, raw)) from e
