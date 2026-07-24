import base64
import hashlib
import json
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from captcha import verify_and_consume_captcha


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_token(secret: str, answer: int, expires: int) -> str:
    iv = b"0123456789ab"
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    payload = json.dumps(
        {"answer": answer, "expires": expires, "nonce": "test-nonce"},
        separators=(",", ":"),
    ).encode("utf-8")
    encrypted_and_tag = AESGCM(key).encrypt(iv, payload, None)
    return ".".join(
        (_encode(iv), _encode(encrypted_and_tag[:-16]), _encode(encrypted_and_tag[-16:]))
    )


class CaptchaTests(unittest.TestCase):
    def test_valid_token_is_accepted_only_once(self):
        secret = "a-test-secret-that-is-at-least-32-characters"
        token = make_token(secret, 12, 20_000)
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertTrue(
                verify_and_consume_captcha(token, "12", secret, data_dir, now_ms=10_000)
            )
            self.assertFalse(
                verify_and_consume_captcha(token, "12", secret, data_dir, now_ms=10_001)
            )

    def test_wrong_answer_burns_the_token(self):
        secret = "a-test-secret-that-is-at-least-32-characters"
        token = make_token(secret, 12, 20_000)
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertFalse(
                verify_and_consume_captcha(token, "11", secret, data_dir, now_ms=10_000)
            )
            # A challenge is worth exactly one attempt, so the correct answer
            # can no longer be replayed against the same token.
            self.assertFalse(
                verify_and_consume_captcha(token, "12", secret, data_dir, now_ms=10_001)
            )

    def test_wrong_answer_secret_and_expired_token_are_rejected(self):
        secret = "a-test-secret-that-is-at-least-32-characters"
        token = make_token(secret, 12, 20_000)
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertFalse(
                verify_and_consume_captcha(token, "11", secret, data_dir, now_ms=10_000)
            )
            self.assertFalse(
                verify_and_consume_captcha(
                    token,
                    "12",
                    "another-secret-that-is-at-least-32-characters",
                    data_dir,
                    now_ms=10_000,
                )
            )
            self.assertFalse(
                verify_and_consume_captcha(token, "12", secret, data_dir, now_ms=20_000)
            )


if __name__ == "__main__":
    unittest.main()
