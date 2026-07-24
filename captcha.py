import base64
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _consume_token(data_dir: str, token: str, expires: int, now: int) -> bool:
    os.makedirs(data_dir, exist_ok=True)
    database = os.path.join(data_dir, "captcha_tokens.sqlite3")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    try:
        with closing(sqlite3.connect(database, timeout=5)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS used_captcha_tokens "
                "(token_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL)"
            )
            connection.execute(
                "DELETE FROM used_captcha_tokens WHERE expires <= ?", (now,)
            )
            connection.execute(
                "INSERT INTO used_captcha_tokens (token_hash, expires) VALUES (?, ?)",
                (token_hash, expires),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def verify_and_consume_captcha(
    token: object,
    answer: object,
    secret: str,
    data_dir: str,
    *,
    now_ms: int | None = None,
) -> bool:
    """Verify an Astro AES-256-GCM CAPTCHA token and reject token replays."""
    if not isinstance(token, str) or not isinstance(answer, str):
        return False
    if not answer.isdigit() or not 1 <= len(answer) <= 2:
        return False

    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return False

    try:
        iv, encrypted, auth_tag = map(_decode_base64url, parts)
        key = hashlib.sha256(secret.encode("utf-8")).digest()
        payload = json.loads(AESGCM(key).decrypt(iv, encrypted + auth_tag, None))
    except (InvalidTag, ValueError, TypeError, json.JSONDecodeError):
        return False

    now = int(time.time() * 1000) if now_ms is None else now_ms
    expected = payload.get("answer") if isinstance(payload, dict) else None
    expires = payload.get("expires") if isinstance(payload, dict) else None
    nonce = payload.get("nonce") if isinstance(payload, dict) else None
    if (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or not isinstance(expires, (int, float))
        or isinstance(expires, bool)
        or not isinstance(nonce, str)
        or not nonce
        or expires <= now
    ):
        return False

    # Burn the token before the answer is checked so that one challenge buys
    # exactly one attempt; otherwise the small answer space could be guessed by
    # replaying the same token.
    if not _consume_token(data_dir, token, int(expires), now):
        return False

    return int(answer) == expected
