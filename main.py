from flask import Flask, request, jsonify
from flask_mail import Mail, Message
from flask_cors import CORS
import os
from dotenv import load_dotenv

app = Flask(__name__)
CORS(app)
load_dotenv()

# Configuration for Flask-Mail
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = os.getenv("PORT")
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

mail = Mail(app)

# Allowed domains and corresponding email addresses
DOMAIN_EMAIL_MAP = {
    "yourdomain.com": ["you@yourdomain.com"],
}


def get_recipients_from_domain(referer):
    if referer is None:
        return None
    for domain in DOMAIN_EMAIL_MAP:
        if domain in referer:
            return DOMAIN_EMAIL_MAP[domain]
    return None


@app.route("/submit", methods=["POST"])
def send_email():
    referer = request.headers.get("Referer")
    recipients = get_recipients_from_domain(referer)
    if recipients is None:
        return jsonify({"error": "Unauthorized domain"}), 403

    data = request.get_json()
    name = data.get("name")
    email = data.get("email")
    message = data.get("message")

    mailmessage = f"Hallo :) Dein Kontaktformular hat soeben eine neue Nachricht an dich abgeschickt:\n\nNam: {data.get("name")}\nMail: {data.get("email")}\nNachricht: {data.get("message")}\n\nEine Antwort auf diese Bee
nachrichtungsmail wird als Antwort an den Absender der Anfrage geschikt.\n\nHab einen super Tag!"

    msg = Message(
        subject=f"Message from {name}",
        recipients=recipients,
        body=mailmessage,
        reply_to=email,
    )

    try:
        mail.send(msg)
        return jsonify({"message": "Message sent successfully!"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(port=5000)
