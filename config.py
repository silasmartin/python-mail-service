import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SmtpSettings:
    server: str
    port: int
    use_tls: bool
    use_ssl: bool
    username: str | None
    password: str | None
    default_sender: str


@dataclass(frozen=True)
class DomainSettings:
    domain: str
    recipients: tuple[str, ...]
    captcha_secret: str
    smtp: SmtpSettings


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() == "true"


def _global_smtp_defaults() -> dict:
    return {
        "server": os.getenv("MAIL_SERVER"),
        "port": os.getenv("MAIL_PORT", "587"),
        "use_tls": _env_bool("MAIL_USE_TLS", True),
        "use_ssl": _env_bool("MAIL_USE_SSL", False),
        "username": os.getenv("MAIL_USERNAME"),
        "password": os.getenv("MAIL_PASSWORD"),
        "default_sender": os.getenv("MAIL_DEFAULT_SENDER"),
    }


def _domain_config_from_legacy_env() -> dict:
    raw = os.getenv("DOMAIN_EMAIL_MAP")
    if not raw:
        return {}
    try:
        recipients = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DOMAIN_EMAIL_MAP is not valid JSON: {exc}") from exc
    if not isinstance(recipients, dict):
        raise RuntimeError("DOMAIN_EMAIL_MAP must be a JSON object")

    captcha_secret = os.getenv("CAPTCHA_SECRET")
    return {
        domain: {
            "recipients": value,
            "captcha_secret": captcha_secret,
        }
        for domain, value in recipients.items()
    }


def _parse_smtp(domain: str, overrides: object, defaults: dict) -> SmtpSettings:
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise RuntimeError(f"DOMAIN_CONFIG[{domain!r}].smtp must be an object")

    values = {**defaults, **overrides}
    required = ("server", "default_sender")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise RuntimeError(
            f"Missing SMTP settings for {domain}: {', '.join(missing)}"
        )

    try:
        port = int(values["port"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid SMTP port for {domain}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"Invalid SMTP port for {domain}")

    use_tls = values.get("use_tls", True)
    use_ssl = values.get("use_ssl", False)
    if not isinstance(use_tls, bool) or not isinstance(use_ssl, bool):
        raise RuntimeError(f"SMTP use_tls/use_ssl values for {domain} must be booleans")
    if use_tls and use_ssl:
        raise RuntimeError(f"SMTP TLS and SSL cannot both be enabled for {domain}")

    username = values.get("username")
    password = values.get("password")
    if bool(username) != bool(password):
        raise RuntimeError(
            f"SMTP username and password must either both be set or both be omitted for {domain}"
        )

    return SmtpSettings(
        server=str(values["server"]),
        port=port,
        use_tls=use_tls,
        use_ssl=use_ssl,
        username=str(username) if username else None,
        password=str(password) if password else None,
        default_sender=str(values["default_sender"]),
    )


def load_domain_config() -> dict[str, DomainSettings]:
    """Load per-domain routing, CAPTCHA and optional SMTP overrides."""
    raw = os.getenv("DOMAIN_CONFIG")
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"DOMAIN_CONFIG is not valid JSON: {exc}") from exc
    else:
        parsed = _domain_config_from_legacy_env()

    if not isinstance(parsed, dict):
        raise RuntimeError("DOMAIN_CONFIG must be a JSON object")

    defaults = _global_smtp_defaults()
    result = {}
    for raw_domain, value in parsed.items():
        if not isinstance(raw_domain, str):
            raise RuntimeError("DOMAIN_CONFIG domain keys must be strings")
        domain = raw_domain.strip().lower().rstrip(".")
        if not domain or any(char in domain for char in "/:@"):
            raise RuntimeError(f"Invalid domain in DOMAIN_CONFIG: {raw_domain!r}")
        if not isinstance(value, dict):
            raise RuntimeError(f"DOMAIN_CONFIG[{domain!r}] must be an object")

        recipients = value.get("recipients")
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list) or not recipients or not all(
            isinstance(address, str) and address.strip() for address in recipients
        ):
            raise RuntimeError(f"Recipients for {domain} must be a non-empty list")

        captcha_secret = value.get("captcha_secret")
        if not isinstance(captcha_secret, str) or len(captcha_secret) < 32:
            raise RuntimeError(
                f"CAPTCHA secret for {domain} must contain at least 32 characters"
            )

        result[domain] = DomainSettings(
            domain=domain,
            recipients=tuple(address.strip() for address in recipients),
            captcha_secret=captcha_secret,
            smtp=_parse_smtp(domain, value.get("smtp"), defaults),
        )

    return result
