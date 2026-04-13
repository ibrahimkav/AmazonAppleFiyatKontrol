"""Uyarı mesajlarına isteğe bağlı kısa Türkçe özet (GROQ_API_KEY doluysa)."""

from __future__ import annotations

from groq_client import groq_chat_completion


def generate_alert_comment(
    *,
    api_key: str,
    model: str,
    product_name: str,
    detail_lines: list[str],
) -> str | None:
    if not api_key.strip():
        return None
    ctx = "\n".join(detail_lines)
    prompt = f"""Sen bir alışveriş asistanısın. Aşağıdaki Amazon TR fiyat uyarısını oku.

Ürün / arama: {product_name}
Veri:
{ctx}

Görev: Türkçe, en fazla 2 kısa cümle ile bu fiyatın ne anlama geldiğini özetle.
Yatırım tavsiyesi verme, "al/sat" deme, sadece bilgilendir.
Emoji kullanma veya en fazla bir tane kullan."""
    try:
        text = groq_chat_completion(
            api_key=api_key,
            model=model,
            user_message=prompt,
            max_tokens=256,
            temperature=0.4,
            timeout_sec=45,
        )
        return text if text else None
    except Exception:
        return None
