# Python Mail Service

A small Flask service that receives contact-form submissions, emails them to the
configured recipient, and optionally sends a Telegram notification.

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

Required: `MAIL_SERVER`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`,
`DOMAIN_EMAIL_MAP`, and `CORS_ORIGINS`. Telegram variables are optional.

`DOMAIN_EMAIL_MAP` is a JSON object mapping a domain (matched against the request
`Referer` header) to the recipient address(es):

```json
{"yourdomain.com": ["you@yourdomain.com"]}
```

## Run (production)

```bash
docker compose up -d --build
```

The container runs under gunicorn as a non-root user, exposes port `8004`, and
ships a `/health` endpoint used by the Docker healthcheck.

## API

- `POST /submit` — JSON body with arbitrary form fields; every field is included
  in the notification email. `email` (if present) is used as Reply-To and `name`
  in the subject. The request `Referer` must match a configured domain. Returns
  `200` on success, `400` for invalid input, `403` for an unauthorized domain.
  See [docs/SUBMITTING.md](docs/SUBMITTING.md) for the full contract.
- `GET /health` — liveness probe, returns `{"status": "ok"}`.

## Local development

```bash
pip install -r requirements.txt
python main.py   # dev server on 127.0.0.1:8004
```
