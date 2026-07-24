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

    def _load(self, smtp_overrides, environment=None):
        config = {
            "example.com": {
                "recipients": ["recipient@example.com"],
                "captcha_secret": "captcha-secret-with-at-least-32-characters",
                "smtp": smtp_overrides,
            }
        }
        env = {
            "DOMAIN_CONFIG": json.dumps(config),
            "MAIL_SERVER": "smtp.shared.example",
            "MAIL_PORT": "587",
            "MAIL_USE_TLS": "true",
            "MAIL_USE_SSL": "false",
            "MAIL_USERNAME": "shared-user",
            "MAIL_PASSWORD": "shared-password",
            "MAIL_DEFAULT_SENDER": "Shared Website <web@shared.example>",
            **(environment or {}),
        }
        with patch.dict(os.environ, env, clear=True):
            return load_domain_config()["example.com"]

    def test_domain_can_override_server_port_and_transport(self):
        settings = self._load(
            {
                "server": "smtp.domain.example",
                "port": 465,
                "use_ssl": True,
                "default_sender": "Domain Website <web@example.com>",
            }
        )

        self.assertEqual(settings.smtp.server, "smtp.domain.example")
        self.assertEqual(settings.smtp.port, 465)
        self.assertTrue(settings.smtp.use_ssl)
        # Not inherited from MAIL_USE_TLS, otherwise this would be a conflict.
        self.assertFalse(settings.smtp.use_tls)

    def test_domain_switching_to_starttls_does_not_inherit_shared_ssl(self):
        settings = self._load(
            {"use_tls": True},
            environment={"MAIL_USE_TLS": "false", "MAIL_USE_SSL": "true"},
        )

        self.assertTrue(settings.smtp.use_tls)
        self.assertFalse(settings.smtp.use_ssl)

    def test_domain_without_transport_overrides_inherits_shared_settings(self):
        settings = self._load(
            {"server": "smtp.domain.example"},
            environment={"MAIL_USE_TLS": "false", "MAIL_USE_SSL": "true"},
        )

        self.assertFalse(settings.smtp.use_tls)
        self.assertTrue(settings.smtp.use_ssl)
        self.assertEqual(settings.smtp.port, 587)

    def test_domain_cannot_enable_tls_and_ssl_together(self):
        with self.assertRaises(RuntimeError):
            self._load({"use_tls": True, "use_ssl": True})

    def test_invalid_port_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self._load({"port": "not-a-number"})


if __name__ == "__main__":
    unittest.main()
