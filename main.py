import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from captcha import verify_and_consume_captcha
from config import load_domain_config
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from mailer import send_email
from notify import send_ops_alert
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mail-service")

# --- Configuration -----------------------------------------------------------

# Limits on the submitted form (defense against abuse). The form is variable:
# any set of fields is accepted, within these bounds.
MAX_FIELDS = 50
MAX_KEY_LEN = 100
MAX_VALUE_LEN = 10000
MAX_EMAIL_LEN = 320

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 'name' and 'email' carry meaning for the service, so their keys are fixed and
# lowercase. Every other field is rendered under the label the form submitted,
# so only these two need a presentable form in the email body.
DISPLAY_LABELS = {"name": "Name", "email": "Email"}


def create_app():
    app = Flask(__name__)

    # Respect X-Forwarded-* headers when running behind a reverse proxy so that
    # the client IP and scheme are reported correctly.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # 64 KiB request body cap

    app.config["DOMAIN_CONFIG"] = load_domain_config()
    if not app.config["DOMAIN_CONFIG"]:
        logger.warning("DOMAIN_CONFIG is empty; all /submit requests will be rejected")

    app.config["DATA_DIR"] = os.getenv("DATA_DIR", "/usr/src/app/data")

    # Restrict CORS to the configured origins (comma-separated). Defaults to none.
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    CORS(app, resources={r"/submit": {"origins": origins or []}})

    register_routes(app)
    return app


# --- Helpers -----------------------------------------------------------------


def get_domain_settings(domain_config, origin, referer):
    source = origin or referer
    if not source:
        return None
    try:
        hostname = urlsplit(source).hostname
    except ValueError:
        return None
    if not hostname:
        return None
    return domain_config.get(hostname.lower().rstrip("."))


def send_email_in_background(smtp, recipients, subject, body, reply_to, data_dir, domain):
    """Deliver the notification email, buffering the inquiry if delivery fails.

    Submissions are never written to disk on the happy path - the mailbox is
    the system of record. Only a failed delivery is buffered, so that an
    inquiry is not silently lost, and the operator is alerted to recover it.
    """
    try:
        send_email(smtp, recipients, subject, body, reply_to)
        logger.info("Notification email sent to %s", recipients)
        return
    except Exception as error:  # noqa: BLE001 - log and swallow; this runs detached
        logger.exception("Failed to send notification email")
        # `error` is unbound once the except block ends, so keep what is needed.
        error_type = type(error).__name__

    reference = _buffer_failed_submission(data_dir, subject, body, recipients)

    # Operational facts only: the exception message and the recipients may
    # contain personal data, the exception type does not.
    send_ops_alert(
        "Mail delivery failed.\n"
        f"Domain: {domain}\n"
        f"Error: {error_type}\n"
        + (
            f"The inquiry was buffered as {reference} and can be recovered."
            if reference
            else "The inquiry could NOT be buffered and is lost."
        )
    )


def _buffer_failed_submission(data_dir, subject, body, recipients):
    """Write an undeliverable submission to disk; return its filename or None."""
    directory = os.path.join(data_dir, "failed")
    name = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.txt"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
            fh.write(f"To: {', '.join(recipients)}\nSubject: {subject}\n\n{body}")
    except OSError:
        logger.exception("Failed to buffer undeliverable submission in %s", directory)
        return None
    logger.warning("Buffered undeliverable submission as %s", name)
    return name


def _validate_payload(data):
    """Validate a variable form submission.

    Accepts any JSON object of field -> scalar value. Returns (fields, error)
    where fields is an ordered dict of trimmed string values on success, or
    error is a human-readable message on failure.
    """
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object"
    if not data:
        return None, "Request body must contain at least one field"
    if len(data) > MAX_FIELDS:
        return None, f"Too many fields (max {MAX_FIELDS})"

    fields = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            return None, "Field names must be non-empty strings"
        if len(key) > MAX_KEY_LEN:
            return None, "A field name exceeds the maximum allowed length"
        if value is None:
            text = ""
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            text = str(value).strip()
        else:
            return None, f"Field '{key.strip()}' must be a string, number or boolean"
        if len(text) > MAX_VALUE_LEN:
            return None, f"Field '{key.strip()}' exceeds the maximum allowed length"
        fields[key.strip()] = text

    if not any(fields.values()):
        return None, "At least one field must have a value"

    # 'email', if supplied, is used as the reply-to address, so validate it.
    email = fields.get("email")
    if email and (len(email) > MAX_EMAIL_LEN or not EMAIL_RE.match(email)):
        return None, "Invalid email address"

    return fields, None


# --- Routes ------------------------------------------------------------------


def register_routes(app):
    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    @app.route("/submit", methods=["POST"])
    def submit():
        domain_settings = get_domain_settings(
            app.config["DOMAIN_CONFIG"],
            request.headers.get("Origin"),
            request.headers.get("Referer"),
        )
        if domain_settings is None:
            return jsonify({"error": "Unauthorized domain"}), 403

        data = request.get_json(silent=True)
        fields, error = _validate_payload(data)
        if error:
            return jsonify({"error": error}), 400

        # Silently accept honeypot submissions so bots do not learn why their
        # request was discarded.
        if fields.pop("website", ""):
            return jsonify({"message": "Message received successfully!"}), 200

        captcha_answer = fields.pop("captchaAnswer", "")
        captcha_token = fields.pop("captchaToken", "")
        if not verify_and_consume_captcha(
            captcha_token,
            captcha_answer,
            domain_settings.captcha_secret,
            app.config["DATA_DIR"],
        ):
            return jsonify({"error": "Invalid or expired CAPTCHA"}), 422

        # 'name' and 'email' get special treatment when present; every other
        # field is rendered into the body as submitted.
        name = fields.get("name")
        email = fields.get("email")

        field_lines = "\n".join(
            f"{DISPLAY_LABELS.get(key, key)}: {value}"
            for key, value in fields.items()
            if value
        )
        reply_note = (
            "Eine Antwort auf diese Benachrichtigung wird als Antwort an den Absender der Anfrage geschickt.\n\n"
            if email
            else ""
        )
        mailmessage = (
            "Neue Anfrage über das Kontaktformular:\n\n"
            f"{field_lines}\n\n"
            f"{reply_note}"
        )

        subject = (
            f"Message from {name}"
            if name
            else "Neue Nachricht über dein Kontaktformular"
        )

        threading.Thread(
            target=send_email_in_background,
            args=(
                domain_settings.smtp,
                domain_settings.recipients,
                subject,
                mailmessage,
                email or None,
                app.config["DATA_DIR"],
                domain_settings.domain,
            ),
            daemon=True,
        ).start()

        return jsonify({"message": "Message received successfully!"}), 200

    @app.errorhandler(413)
    def payload_too_large(_):
        return jsonify({"error": "Payload too large"}), 413

    @app.errorhandler(500)
    def internal_error(_):
        return jsonify({"error": "Internal server error"}), 500


app = create_app()


if __name__ == "__main__":
    # Development entrypoint only. Production uses gunicorn (see Dockerfile).
    app.run(host="127.0.0.1", port=int(os.getenv("MAIL_PORT_HTTP", "8004")))
