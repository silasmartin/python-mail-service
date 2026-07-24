import logging
import os

import requests

logger = logging.getLogger("mail-service")

TELEGRAM_API = "https://api.telegram.org"


def _configured(value: str | None) -> str | None:
    """Return a usable env value, or None if unset, blank or a placeholder.

    Placeholders matter: a literal `<your_bot_token>` is a non-empty string and
    would otherwise pass a naive truthiness check, causing live alerts to be
    posted against a bogus token.
    """
    if not value:
        return None
    value = value.strip()
    if not value or value.startswith("<"):
        return None
    return value


def send_ops_alert(text: str) -> None:
    """Notify the operator that the service itself is having a problem.

    Alerts travel to Telegram, i.e. outside the EU, so they carry operational
    facts only - never submitted form data, recipient addresses or exception
    messages, any of which may contain personal data. Callers are responsible
    for keeping the text free of it.

    Never raises: alerting is best-effort and must not affect request handling.
    """
    token = _configured(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = _configured(os.getenv("TELEGRAM_CHAT_ID"))
    if not token or not chat_id:
        return

    try:
        response = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to send ops alert")
