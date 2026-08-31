import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

TEST_DATA_DIR = tempfile.TemporaryDirectory()
SECRET_A = "endpoint-test-secret-with-at-least-32-characters"
SECRET_B = "another-endpoint-secret-with-at-least-32-chars!!"

os.environ.update(
    {
        "DOMAIN_CONFIG": json.dumps(
            {
                "example.com": {
                    "recipients": ["recipient@example.com"],
                    "captcha_secret": SECRET_A,
                    "smtp": {
                        "server": "smtp.example.com",
                        "default_sender": "Website <web@example.com>",
                    },
                },
                "other.example": {
                    "recipients": ["recipient@other.example"],
                    "captcha_secret": SECRET_B,
                    "smtp": {
                        "server": "smtp.other.example",
                        "default_sender": "Website <web@other.example>",
                    },
                },
            }
        ),
        "DATA_DIR": TEST_DATA_DIR.name,
        "CORS_ORIGINS": "https://example.com",
    }
)

try:
    import main  # noqa: E402 - environment must be configured before app creation
    from captcha import create_captcha, verify_and_consume_captcha  # noqa: E402
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"service dependencies are not installed: {exc.name}") from exc


QUESTION_RE = re.compile(r"^([1-9]) \+ ([1-9]) =$")


def solve(question: str) -> str:
    match = QUESTION_RE.match(question)
    assert match, f"unexpected question format: {question!r}"
    return str(int(match.group(1)) + int(match.group(2)))


class CreateCaptchaTests(unittest.TestCase):
    def test_challenge_verifies_against_the_same_secret(self):
        challenge = create_captcha(SECRET_A)
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertTrue(
                verify_and_consume_captcha(
                    challenge["token"], solve(challenge["question"]), SECRET_A, data_dir
                )
            )

    def test_challenge_is_single_use(self):
        challenge = create_captcha(SECRET_A)
        answer = solve(challenge["question"])
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertTrue(
                verify_and_consume_captcha(challenge["token"], answer, SECRET_A, data_dir)
            )
            self.assertFalse(
                verify_and_consume_captcha(challenge["token"], answer, SECRET_A, data_dir)
            )

    def test_wrong_answer_is_rejected(self):
        challenge = create_captcha(SECRET_A)
        wrong = str(int(solve(challenge["question"])) + 1)
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertFalse(
                verify_and_consume_captcha(challenge["token"], wrong, SECRET_A, data_dir)
            )

    def test_token_of_one_domain_does_not_verify_for_another(self):
        challenge = create_captcha(SECRET_A)
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertFalse(
                verify_and_consume_captcha(
                    challenge["token"], solve(challenge["question"]), SECRET_B, data_dir
                )
            )

    def test_expired_challenge_is_rejected(self):
        challenge = create_captcha(SECRET_A, now_ms=0)
        with tempfile.TemporaryDirectory() as data_dir:
            # Ten minutes and one millisecond later.
            self.assertFalse(
                verify_and_consume_captcha(
                    challenge["token"],
                    solve(challenge["question"]),
                    SECRET_A,
                    data_dir,
                    now_ms=10 * 60 * 1000 + 1,
                )
            )

    def test_challenges_differ_between_calls(self):
        tokens = {create_captcha(SECRET_A)["token"] for _ in range(20)}
        self.assertEqual(len(tokens), 20)

    def test_short_secret_is_refused(self):
        with self.assertRaises(ValueError):
            create_captcha("too-short")


class CaptchaEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()

    def test_returns_a_solvable_challenge_for_a_configured_domain(self):
        response = self.client.get("/api/captcha", headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn("question", body)
        self.assertIn("token", body)
        self.assertRegex(body["question"], QUESTION_RE)
        self.assertEqual(len(body["token"].split(".")), 3)

    def test_challenge_from_the_endpoint_is_accepted_by_submit(self):
        challenge = self.client.get(
            "/api/captcha", headers={"Origin": "https://example.com"}
        ).get_json()

        with patch("main.threading.Thread") as thread:
            response = self.client.post(
                "/submit",
                headers={"Origin": "https://example.com"},
                json={
                    "name": "Maria Beispiel",
                    "email": "maria@example.org",
                    "Nachricht": "Anfrage für eine Trauerfeier.",
                    "captchaAnswer": solve(challenge["question"]),
                    "captchaToken": challenge["token"],
                },
            )
        self.assertEqual(response.status_code, 200)
        thread.assert_called_once()

    def test_submit_rejects_a_reused_challenge_with_422(self):
        challenge = self.client.get(
            "/api/captcha", headers={"Origin": "https://example.com"}
        ).get_json()
        payload = {
            "name": "Maria Beispiel",
            "email": "maria@example.org",
            "Nachricht": "Anfrage für eine Hochzeit.",
            "captchaAnswer": solve(challenge["question"]),
            "captchaToken": challenge["token"],
        }

        with patch("main.threading.Thread"):
            first = self.client.post(
                "/submit", headers={"Origin": "https://example.com"}, json=payload
            )
        second = self.client.post(
            "/submit", headers={"Origin": "https://example.com"}, json=payload
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 422)

    def test_challenge_is_never_cached(self):
        response = self.client.get("/api/captcha", headers={"Origin": "https://example.com"})
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_unknown_domain_is_rejected(self):
        response = self.client.get("/api/captcha", headers={"Origin": "https://not-configured.test"})
        self.assertEqual(response.status_code, 403)

    def test_missing_origin_and_referer_is_rejected(self):
        self.assertEqual(self.client.get("/api/captcha").status_code, 403)

    def test_referer_is_accepted_when_origin_is_absent(self):
        response = self.client.get(
            "/api/captcha", headers={"Referer": "https://example.com/kontakt/"}
        )
        self.assertEqual(response.status_code, 200)

    def test_each_domain_gets_a_token_only_its_own_secret_can_verify(self):
        for_a = self.client.get(
            "/api/captcha", headers={"Origin": "https://example.com"}
        ).get_json()

        with tempfile.TemporaryDirectory() as data_dir:
            self.assertFalse(
                verify_and_consume_captcha(
                    for_a["token"], solve(for_a["question"]), SECRET_B, data_dir
                )
            )

    def test_endpoint_is_read_only(self):
        self.assertEqual(
            self.client.post("/api/captcha", headers={"Origin": "https://example.com"}).status_code,
            405,
        )


class ExistingBehaviourTests(unittest.TestCase):
    """The endpoint is additive - nothing about the old contract may change."""

    def setUp(self):
        self.client = main.app.test_client()

    def test_health_still_answers(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_submit_still_requires_a_captcha(self):
        # A site that never calls /api/captcha but signs its own token keeps
        # working; one that sends no token at all is still turned away.
        response = self.client.post(
            "/submit",
            headers={"Origin": "https://example.com"},
            json={"name": "Bot", "Nachricht": "kein Captcha"},
        )
        self.assertEqual(response.status_code, 422)

    def test_submit_still_swallows_honeypot_submissions(self):
        response = self.client.post(
            "/submit",
            headers={"Origin": "https://example.com"},
            json={"name": "Bot", "Nachricht": "spam", "website": "http://spam.example"},
        )
        self.assertEqual(response.status_code, 200)

    def test_submit_still_rejects_unknown_domains(self):
        response = self.client.post(
            "/submit",
            headers={"Origin": "https://not-configured.test"},
            json={"name": "X"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
