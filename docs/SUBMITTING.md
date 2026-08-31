# Integration Guide: Submitting to the Mail Service

Instructions for an agent (or developer) wiring a contact form up to this
service. Follow these exactly — the common integration issues are domain
matching, CAPTCHA secrets, and the CORS allow-list.

## Endpoint

```
POST /submit
Content-Type: application/json
```

The service listens on `8004` but is bound to `127.0.0.1` (see
`docker-compose.yaml`), so it is **not** reachable directly from the internet.
Expose it through a reverse proxy (nginx/Caddy/Traefik) on your public domain,
e.g. `https://yourdomain.com/api/submit -> 127.0.0.1:8004/submit`.

## Request body

The form is **variable** — send a JSON object with whatever fields your form
has. Every field you send is rendered into the notification email (and the
persisted log) as `field: value`, in the order submitted. None of the field
names are mandatory:

```json
{
  "name": "Alice Example",
  "email": "alice@example.com",
  "company": "Acme Inc.",
  "budget": 5000,
  "message": "Hello, I'd like to get in touch.",
  "captchaAnswer": "12",
  "captchaToken": "signed-token-from-the-challenge-endpoint"
}
```

Two field names get special handling **when present** (both optional):

- `email` — used as the email `Reply-To` so you can reply straight to the
  sender. If present it must be a valid address.
- `name` — used in the email subject (`Neue Kontaktanfrage von <name>`). Falls
  back to `Neue Kontaktanfrage über <domain>` when absent.

The notification email is German. `name`, `email` and `message` are rendered as
`Name:` / `E-Mail:` / `Nachricht:`; every other field keeps the label you
submitted it under, with its first letter capitalised. Fields containing line
breaks get their own indented block so the sender's formatting survives, and the
mail is timestamped in Europe/Berlin.

One further field name is reserved:

- `website` — a honeypot. Render a hidden, non-focusable input under this name
  and leave it empty. Submissions where it is non-empty are discarded silently
  and still answered with `200`, so bots learn nothing.

Values must be scalars — strings, numbers, or booleans. Nested objects/arrays
are rejected.

`captchaAnswer` and `captchaToken` are required. They are validated and removed
before the notification is persisted or sent. The token is encrypted and
authenticated with the submitting domain's `captcha_secret` and expires after
ten minutes.

**A token is worth exactly one attempt.** It is consumed whether or not the
answer is correct, so a `422` means the client must fetch a *new* challenge —
resubmitting the same token, even with the right answer, will fail again.

### Getting a challenge

Two ways, both producing the same token format and both verified identically:

- **The site signs its own.** A server-rendered site that holds the same
  `CAPTCHA_SECRET` builds the token itself. Nothing else is needed; this is how
  the existing integrations work and none of them have to change.
- **`GET /api/captcha` on this service.** For a statically built site, which has
  no server of its own to sign anything. The endpoint is optional; it exists
  only for this case.

```http
GET /api/captcha
Origin: https://yourdomain.com
```

```json
{ "question": "3 + 7 =", "token": "<iv>.<ct>.<tag>" }
```

The calling hostname must be a key in `DOMAIN_CONFIG`, exactly as for `/submit`;
an unknown domain gets `403`. The response carries `Cache-Control: no-store` —
a cached challenge would be handed to several visitors and burnt by the first
submission. Fetch a fresh one when the form loads, and again after every `422`.

Constraints (enforced server-side — violating them returns `400`):

| Rule                                                              |
|-------------------------------------------------------------------|
| at least one field, with at least one non-empty value             |
| at most 50 fields; each field name ≤ 100 chars                    |
| each value ≤ 10 000 chars (after trimming)                        |
| `email`, if present, must match `local@domain.tld` and be ≤ 320   |
| total request body ≤ 64 KiB                                       |

## Authorization: submitting hostname

There is no API key. Authorization is by exact hostname. The service uses the
request's `Origin` header when present and otherwise falls back to `Referer`.
That hostname must be a key in `DOMAIN_CONFIG`; the matched entry determines
the recipients, CAPTCHA secret, and SMTP credentials.

- **From a browser** (`fetch`/`XMLHttpRequest`): the browser sets `Referer`
  automatically to the page URL, so as long as the form is served from a
  configured domain, this just works. Do **not** try to set `Referer` manually
  in browser code — it's a forbidden header and will be ignored.
- **From a server / script / agent**: there is no browser, so you must set the
  `Referer` header yourself to a URL on a configured domain.

If both headers are missing or the selected header matches no configured
domain, the service returns `403 {"error": "Unauthorized domain"}`.

## CORS (browser submissions only)

For cross-origin browser requests, the page's origin must be listed in the
service's `CORS_ORIGINS` env var (comma-separated). If your form is served from
the same origin that proxies to the service, CORS does not apply. Server-side
callers ignore CORS entirely.

## Responses

| Status | Meaning                                   | Body                                          |
|--------|-------------------------------------------|-----------------------------------------------|
| `200`  | Accepted (email sent async)               | `{"message": "Message received successfully!"}` |
| `400`  | Invalid/missing fields or bad email       | `{"error": "<reason>"}`                       |
| `403`  | Origin/referer hostname is unauthorized    | `{"error": "Unauthorized domain"}`            |
| `422`  | CAPTCHA invalid, expired, or already used  | `{"error": "Invalid or expired CAPTCHA"}`     |
| `413`  | Request body exceeded 64 KiB              | `{"error": "Payload too large"}`              |

A `200` means the submission was accepted and queued — the email is sent on a
background thread, so delivery failures are **not** reflected in the HTTP
response. They are logged, the inquiry is buffered on the server so it can be
recovered, and the operator is alerted.

## Examples

### Browser (same-origin or CORS-allowed origin)

```js
const res = await fetch("https://yourdomain.com/api/submit", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name, email, message, captchaAnswer, captchaToken }),
});
if (!res.ok) {
  const { error } = await res.json();
  throw new Error(error ?? `Request failed: ${res.status}`);
}
```

### Server-side / agent (must set `Referer` manually)

```bash
curl -fsS https://yourdomain.com/api/submit \
  -H "Content-Type: application/json" \
  -H "Referer: https://yourdomain.com/contact" \
  -d '{"name":"Alice","email":"alice@example.com","message":"Hello","captchaAnswer":"12","captchaToken":"..."}'
```

```python
import requests

requests.post(
    "https://yourdomain.com/api/submit",
    json={"name": "Alice", "email": "alice@example.com", "message": "Hello", "captchaAnswer": "12", "captchaToken": "..."},
    headers={"Referer": "https://yourdomain.com/contact"},
    timeout=10,
).raise_for_status()
```

## Checklist for a working integration

1. The submitting page/script's exact hostname is a key in `DOMAIN_CONFIG`.
2. Its `captcha_secret` equals the website's `CAPTCHA_SECRET` — or, for a static
   site fetching challenges from `GET /api/captcha`, the site needs no secret at
   all and only this service holds it.
3. Browser forms are served from an origin listed in `CORS_ORIGINS` (this
   covers `/api/captcha` as well as `/submit`; same-origin proxying needs no
   CORS at all).
4. Server-side callers set a `Referer` header on a configured domain.
5. Body is a JSON object with one or more scalar fields, within the limits
   above (include `email` if you want replies to reach the sender).
6. The public reverse proxy forwards to `127.0.0.1:8004`.
```
