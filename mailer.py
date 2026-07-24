import smtplib
import ssl
from email.message import EmailMessage

from config import SmtpSettings


def send_email(
    smtp: SmtpSettings,
    recipients: tuple[str, ...],
    subject: str,
    body: str,
    reply_to: str | None,
) -> None:
    message = EmailMessage()
    message["From"] = smtp.default_sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)

    smtp_class = smtplib.SMTP_SSL if smtp.use_ssl else smtplib.SMTP
    context = ssl.create_default_context()
    kwargs = {"host": smtp.server, "port": smtp.port, "timeout": 20}
    if smtp.use_ssl:
        kwargs["context"] = context

    with smtp_class(**kwargs) as connection:
        if smtp.use_tls:
            connection.starttls(context=context)
        if smtp.username and smtp.password:
            connection.login(smtp.username, smtp.password)
        connection.send_message(message)
