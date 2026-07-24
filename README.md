# Python Mail Service

A small Flask service that receives contact-form submissions and emails them to
the configured recipient.

Submissions are never written to disk on the happy path - the mailbox is the
system of record. Only if SMTP delivery fails is the inquiry buffered under
`<DATA_DIR>/failed/` so it can be recovered, and an operational alert is sent
(see below).

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

`DOMAIN_CONFIG` is required. Shared `MAIL_*` values provide SMTP defaults;
individual domains can override any or all of them. The Telegram variables are
optional and only drive operational alerts.

`DOMAIN_CONFIG` is a JSON object mapping an exact submitting hostname to its
recipients, CAPTCHA secret, and optional SMTP overrides:

```json
{
  "yourdomain.com": {
    "recipients": ["you@yourdomain.com"],
    "captcha_secret": "at-least-32-random-characters-long",
    "smtp": {
      "server": "smtp.yourdomain.com",
      "port": 465,
      "use_tls": false,
      "use_ssl": true,
      "username": "website@yourdomain.com",
      "password": "secret",
      "default_sender": "Website <website@yourdomain.com>"
    }
  }
}
```

Every key in `smtp` is optional and falls back to the matching `MAIL_*` default:

| Key | Falls back to | Default | Notes |
| --- | --- | --- | --- |
| `server` | `MAIL_SERVER` | - | Required, per domain or shared |
| `port` | `MAIL_PORT` | `587` | Number or numeric string |
| `use_tls` | `MAIL_USE_TLS` | `true` | STARTTLS on a plain connection (587) |
| `use_ssl` | `MAIL_USE_SSL` | `false` | Implicit TLS from the start (465) |
| `username` | `MAIL_USERNAME` | - | Omit together with `password` for an unauthenticated relay |
| `password` | `MAIL_PASSWORD` | - | Omit together with `username` |
| `default_sender` | `MAIL_DEFAULT_SENDER` | - | Required, per domain or shared; used as `From` |

`use_tls` and `use_ssl` are mutually exclusive. Setting either one in a domain's
`smtp` block selects the transport for that domain outright - the other mode
defaults to `false` instead of being inherited, so a domain on port 465 only
needs `"use_ssl": true`.

The CAPTCHA secret must be identical to `CAPTCHA_SECRET` on the corresponding
Astro website. For compatibility, the old `DOMAIN_EMAIL_MAP` format still works
with global `MAIL_*` settings and a global `CAPTCHA_SECRET`.

## Operational alerts

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to be notified when the service
has a problem - currently, when SMTP delivery fails. Leave either blank to
disable alerting entirely.

Alerts are strictly operational and report only the affected domain, the
exception type, and the buffer filename to recover the inquiry from. They carry
**no** form data, recipient addresses or exception messages, since Telegram is a
third-country recipient. Keep it that way when adding new alerts.

## Run (production)

```bash
docker compose up -d --build
```

The container runs under gunicorn as a non-root user (uid `10001`), exposes port
`8004`, and ships a `/health` endpoint used by the Docker healthcheck.

`./data` is bind-mounted into the container, so it must be writable by uid
`10001`. A short-lived `init_data` service takes care of that on every
`docker compose up`; if you run the app without compose, chown the directory
yourself:

```bash
sudo chown -R 10001:10001 ./data
```

## API

- `POST /submit` — JSON body with arbitrary form fields plus `captchaAnswer` and
  `captchaToken`. `email` (if present) is used as Reply-To and `name` in the
  subject. The exact request origin/referer hostname selects its domain config.
  Returns `200` on success, `400` for invalid input, `403` for an unauthorized
  domain, or `422` for an invalid CAPTCHA.
  See [docs/SUBMITTING.md](docs/SUBMITTING.md) for the full contract.
- `GET /health` — liveness probe, returns `{"status": "ok"}`.

## Local development

```bash
pip install -r requirements.txt
python main.py   # dev server on 127.0.0.1:8004
```
