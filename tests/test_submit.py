import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


TEST_DATA_DIR = tempfile.TemporaryDirectory()
TEST_SECRET = "endpoint-test-secret-with-at-least-32-characters"
os.environ.update(
    {
        "DOMAIN_CONFIG": json.dumps(
            {
                "example.com": {
                    "recipients": ["recipient@example.com"],
                    "captcha_secret": TEST_SECRET,
                    "smtp": {
                        "server": "smtp.example.com",
                        "default_sender": "Website <web@example.com>",
                    },
                }
            }
        ),
        "DATA_DIR": TEST_DATA_DIR.name,
        "CORS_ORIGINS": "https://example.com",
    }
)

try:
    import main  # noqa: E402 - environment must be configured before app creation
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"service dependencies are not installed: {exc.name}") from exc


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_token(answer: int) -> str:
    iv = os.urandom(12)
    key = hashlib.sha256(TEST_SECRET.encode("utf-8")).digest()
    payload = json.dumps(
        {
            "answer": answer,
            "expires": int(time.time() * 1000) + 60_000,
            "nonce": _encode(os.urandom(12)),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encrypted_and_tag = AESGCM(key).encrypt(iv, payload, None)
    return ".".join(
        (_encode(iv), _encode(encrypted_and_tag[:-16]), _encode(encrypted_and_tag[-16:]))
    )


class ImmediateThread:
    """Runs the target synchronously so background work is deterministic."""

    def __init__(self, target=None, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()

    def isolated_data_dir(self):
        """Point the app at an empty data dir for the duration of one test."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        previous = main.app.config["DATA_DIR"]
        main.app.config["DATA_DIR"] = directory
        self.addCleanup(main.app.config.__setitem__, "DATA_DIR", previous)
        return directory

    def payload(self, answer="12", token=None):
        return {
            "name": "Alice",
            "email": "alice@example.com",
            "message": "Hello from the contact form",
            "captchaAnswer": answer,
            "captchaToken": token or make_token(12),
        }

    def test_rejects_unknown_origin_before_captcha(self):
        response = self.client.post(
            "/submit",
            json=self.payload(),
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_rejects_invalid_captcha(self):
        response = self.client.post(
            "/submit",
            json=self.payload(answer="11"),
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(response.status_code, 422)

    @patch("main.threading.Thread")
    def test_honeypot_is_accepted_silently_without_sending(self, thread):
        payload = self.payload()
        payload["website"] = "https://spam.example"
        response = self.client.post(
            "/submit",
            json=payload,
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(thread.call_count, 0)

    @patch("main.threading.Thread")
    def test_accepts_valid_domain_and_captcha(self, thread):
        response = self.client.post(
            "/submit",
            json=self.payload(),
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(thread.call_count, 1)

    @patch("main.threading.Thread", ImmediateThread)
    @patch("main.send_ops_alert")
    @patch("main.send_email")
    def test_successful_delivery_writes_nothing_to_disk(self, send, alert):
        data_dir = self.isolated_data_dir()
        response = self.client.post(
            "/submit",
            json=self.payload(),
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send.call_count, 1)
        alert.assert_not_called()
        self.assertFalse(os.path.isdir(os.path.join(data_dir, "failed")))

    @patch("main.threading.Thread", ImmediateThread)
    @patch("main.send_ops_alert")
    @patch("main.send_email", side_effect=OSError("smtp is down"))
    def test_failed_delivery_buffers_inquiry_and_alerts_without_personal_data(
        self, send, alert
    ):
        data_dir = self.isolated_data_dir()
        response = self.client.post(
            "/submit",
            json=self.payload(),
            headers={"Origin": "https://example.com"},
        )
        # The sender is still told the message was accepted.
        self.assertEqual(response.status_code, 200)

        buffered = os.listdir(os.path.join(data_dir, "failed"))
        self.assertEqual(len(buffered), 1)
        with open(
            os.path.join(data_dir, "failed", buffered[0]), encoding="utf-8"
        ) as fh:
            self.assertIn("Hello from the contact form", fh.read())

        alert.assert_called_once()
        text = alert.call_args.args[0]
        self.assertIn("example.com", text)
        self.assertIn("OSError", text)
        # The alert leaves the EU, so it must not carry submitted data.
        for personal in (
            "Alice",
            "alice@example.com",
            "Hello from the contact form",
            "recipient@example.com",
            "smtp is down",
        ):
            self.assertNotIn(personal, text)


class MailRenderingTests(unittest.TestCase):
    """The notification is read by the operator, so it must stay German."""

    FIELDS = {
        "name": "Max Mustermann",
        "email": "max@example.com",
        "telefon": "+49 170 1234567",
        "message": "Guten Tag,\n\nbitte um Rueckruf.",
    }

    def test_subject_is_german_with_and_without_a_name(self):
        self.assertEqual(
            main.render_subject("Max Mustermann", "example.com"),
            "Neue Kontaktanfrage von Max Mustermann",
        )
        self.assertEqual(
            main.render_subject(None, "example.com"),
            "Neue Kontaktanfrage über example.com",
        )

    def test_body_lists_fields_with_german_labels_and_berlin_timestamp(self):
        # 12:30 UTC is 14:30 in Berlin summer time.
        now = datetime(2026, 7, 24, 12, 30, tzinfo=timezone.utc)
        body = main.render_body(self.FIELDS, "example.com", "max@example.com", now=now)

        self.assertIn("Neue Kontaktanfrage über example.com", body)
        self.assertIn("Eingegangen am 24.07.2026 um 14:30 Uhr", body)
        self.assertIn("Name:", body)
        self.assertIn("E-Mail:", body)
        # Unmapped field names are capitalised, not passed through raw.
        self.assertIn("Telefon:", body)
        self.assertIn(
            "Eine Antwort auf diese E-Mail geht direkt an den Absender der Anfrage.",
            body,
        )

    def test_multiline_field_keeps_its_line_breaks_in_an_indented_block(self):
        body = main.render_body(self.FIELDS, "example.com", "max@example.com")

        self.assertIn("Nachricht:\n  Guten Tag,\n\n  bitte um Rueckruf.", body)

    def test_body_without_reply_address_says_so(self):
        body = main.render_body({"anliegen": "Rueckruf"}, "example.com", None)

        self.assertIn("Anliegen:", body)
        self.assertIn("keine Absenderadresse", body)

    def test_empty_fields_are_omitted(self):
        body = main.render_body(
            {"name": "Max", "telefon": ""}, "example.com", None
        )

        self.assertNotIn("Telefon", body)


if __name__ == "__main__":
    unittest.main()
