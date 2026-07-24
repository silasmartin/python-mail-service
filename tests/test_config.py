import json
import os
import unittest
from unittest.mock import patch

from config import load_domain_config


class DomainConfigTests(unittest.TestCase):
    def test_domain_can_override_shared_smtp_credentials(self):
        config = {
            "example.com": {
                "recipients": ["recipient@example.com"],
                "captcha_secret": "captcha-secret-with-at-least-32-characters",
                "smtp": {
                    "username": "domain-user",
                    "password": "domain-password",
                    "default_sender": "Domain Website <web@example.com>",
                },
            }
        }
        environment = {
            "DOMAIN_CONFIG": json.dumps(config),
            "MAIL_SERVER": "smtp.shared.example",
            "MAIL_PORT": "587",
            "MAIL_USE_TLS": "true",
            "MAIL_USE_SSL": "false",
            "MAIL_USERNAME": "shared-user",
            "MAIL_PASSWORD": "shared-password",
            "MAIL_DEFAULT_SENDER": "Shared Website <web@shared.example>",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = load_domain_config()["example.com"]

        self.assertEqual(settings.smtp.server, "smtp.shared.example")
        self.assertEqual(settings.smtp.username, "domain-user")
        self.assertEqual(settings.smtp.password, "domain-password")
        self.assertEqual(
            settings.smtp.default_sender, "Domain Website <web@example.com>"
        )


if __name__ == "__main__":
    unittest.main()
