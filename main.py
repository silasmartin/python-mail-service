from flask import Flask, request, jsonify
from flask_mail import Mail, Message
from flask_cors import CORS
import os
from dotenv import load_dotenv
import threading

app = Flask(__name__)
CORS(app)
load_dotenv()

# Configuration for Flask-Mail
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
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


def send_email_in_background(msg):
    with app.app_context():
        mail.send(msg)


@app.route("/submit", methods=["POST"])
def submit():
    referer = request.headers.get("Referer")
    recipients = get_recipients_from_domain(referer)
    if recipients is None:
        return jsonify({"error": "Unauthorized domain"}), 403

    data = request.get_json()
    name = data.get("name")
    email = data.get("email")
    message = data.get("message")

    mailmessage = (
        f"Hallo :) Dein Kontaktformular hat soeben eine neue Nachricht an dich abgeschickt:\n\n"
        f"Name: {name}\n"
        f"Mail: {email}\n"
        f"Nachricht: {message}\n\n"
        "Eine Antwort auf diese Benachrichtigung wird als Antwort an den Absender der Anfrage geschickt.\n\n"
        "Hab einen super Tag!"
    )

    msg = Message(
        subject=f"Message from {name}",
        recipients=recipients,
        body=mailmessage,
        reply_to=email,
    )

    # Write data to a file in the mounted volume directory
    data_directory = "/usr/src/app/data"
    os.makedirs(data_directory, exist_ok=True)
    data_file_path = os.path.join(data_directory, "form_data.txt")

    with open(data_file_path, "a") as file:
        file.write(f"Name: {name}\nEmail: {email}\nMessage: {message}\n\n")

    # Send email in the background
    threading.Thread(target=send_email_in_background, args=(msg,)).start()

    return jsonify({"message": "Message received successfully!"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8004)
