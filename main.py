import json
import logging
import os
import re
import threading

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_mail import Mail, Message
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mail-service")

# --- Configuration -----------------------------------------------------------

REQUIRED_ENV = ["MAIL_SERVER", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_DEFAULT_SENDER"]

# Limits on the submitted form (defense against abuse). The form is variable:
# any set of fields is accepted, within these bounds.
MAX_FIELDS = 50
MAX_KEY_LEN = 100
MAX_VALUE_LEN = 10000
MAX_EMAIL_LEN = 320

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load_domain_email_map():
    """Load the domain -> [recipients] map from the DOMAIN_EMAIL_MAP env var (JSON)."""
    raw = os.getenv("DOMAIN_EMAIL_MAP")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DOMAIN_EMAIL_MAP is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("DOMAIN_EMAIL_MAP must be a JSON object of domain -> [emails]")
    # Normalise values to lists.
    return {
        domain: ([emails] if isinstance(emails, str) else list(emails))
        for domain, emails in parsed.items()
    }


def create_app():
    app = Flask(__name__)

    # Respect X-Forwarded-* headers when running behind a reverse proxy so that
    # the client IP and scheme are reported correctly.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
    app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
    app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
    app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # 64 KiB request body cap

    app.config["DOMAIN_EMAIL_MAP"] = _load_domain_email_map()
    if not app.config["DOMAIN_EMAIL_MAP"]:
        logger.warning("DOMAIN_EMAIL_MAP is empty; all /submit requests will be rejected")

    app.config["DATA_DIR"] = os.getenv("DATA_DIR", "/usr/src/app/data")

    # Restrict CORS to the configured origins (comma-separated). Defaults to none.
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    CORS(app, resources={r"/submit": {"origins": origins or []}})

    mail = Mail(app)
    register_routes(app, mail)
    return app


# --- Helpers -----------------------------------------------------------------


def get_recipients_from_domain(domain_map, referer):
    if not referer:
        return None
    for domain, recipients in domain_map.items():
        if domain in referer:
            return recipients
    return None


def send_email_in_background(app, mail, msg):
    with app.app_context():
        try:
            mail.send(msg)
            logger.info("Notification email sent to %s", msg.recipients)
        except Exception:  # noqa: BLE001 - log and swallow; this runs detached
            logger.exception("Failed to send notification email")


def send_telegram_notification(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url, json={"chat_id": chat_id, "text": text}, timeout=10
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to send Telegram notification")


def _persist_submission(data_dir, fields):
    try:
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "form_data.txt")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("".join(f"{key}: {value}\n" for key, value in fields.items()))
            fh.write("\n")
    except OSError:
        logger.exception("Failed to persist submission to %s", data_dir)


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


def register_routes(app, mail):
    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    @app.route("/submit", methods=["POST"])
    def submit():
        recipients = get_recipients_from_domain(
            app.config["DOMAIN_EMAIL_MAP"], request.headers.get("Referer")
        )
        if recipients is None:
            return jsonify({"error": "Unauthorized domain"}), 403

        data = request.get_json(silent=True)
        fields, error = _validate_payload(data)
        if error:
            return jsonify({"error": error}), 400

        # 'name' and 'email' get special treatment when present; every other
        # field is rendered into the body as submitted.
        name = fields.get("name")
        email = fields.get("email")

        field_lines = "\n".join(
            f"{key}: {value}" for key, value in fields.items() if value
        )
        reply_note = (
            "Eine Antwort auf diese Benachrichtigung wird als Antwort an den Absender der Anfrage geschickt.\n\n"
            if email
            else ""
        )
        mailmessage = (
            "Hallo :) Dein Kontaktformular hat soeben eine neue Nachricht an dich abgeschickt:\n\n"
            f"{field_lines}\n\n"
            f"{reply_note}"
            "Hab einen super Tag!"
        )

        msg = Message(
            subject=f"Message from {name}" if name else "Neue Nachricht über dein Kontaktformular",
            recipients=recipients,
            body=mailmessage,
            reply_to=email or None,
        )

        _persist_submission(app.config["DATA_DIR"], fields)

        threading.Thread(
            target=send_email_in_background, args=(app, mail, msg), daemon=True
        ).start()
        threading.Thread(
            target=send_telegram_notification, args=(mailmessage,), daemon=True
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
