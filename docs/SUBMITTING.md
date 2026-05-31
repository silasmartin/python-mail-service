# Integration Guide: Submitting to the Mail Service

Instructions for an agent (or developer) wiring a contact form up to this
service. Follow these exactly — the two things that trip up integrations are the
`Referer`-based domain check and the CORS allow-list.

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
  "message": "Hello, I'd like to get in touch."
}
```

Two field names get special handling **when present** (both optional):

- `email` — used as the email `Reply-To` so you can reply straight to the
  sender. If present it must be a valid address.
- `name` — used in the email subject (`Message from <name>`). Falls back to a
  generic subject when absent.

Values must be scalars — strings, numbers, or booleans. Nested objects/arrays
are rejected.

Constraints (enforced server-side — violating them returns `400`):

| Rule                                                              |
|-------------------------------------------------------------------|
| at least one field, with at least one non-empty value             |
| at most 50 fields; each field name ≤ 100 chars                    |
| each value ≤ 10 000 chars (after trimming)                        |
| `email`, if present, must match `local@domain.tld` and be ≤ 320   |
| total request body ≤ 64 KiB                                       |

## Authorization: the `Referer` header

There is no API key. Authorization is by domain: the request's `Referer` header
must **contain** one of the domains configured in the service's
`DOMAIN_EMAIL_MAP` env var. That matched domain determines who receives the
email.

- **From a browser** (`fetch`/`XMLHttpRequest`): the browser sets `Referer`
  automatically to the page URL, so as long as the form is served from a
  configured domain, this just works. Do **not** try to set `Referer` manually
  in browser code — it's a forbidden header and will be ignored.
- **From a server / script / agent**: there is no browser, so you must set the
  `Referer` header yourself to a URL on a configured domain.

If `Referer` is missing or matches no configured domain, the service returns
`403 {"error": "Unauthorized domain"}`.

## CORS (browser submissions only)

For cross-origin browser requests, the page's origin must be listed in the
service's `CORS_ORIGINS` env var (comma-separated). If your form is served from
the same origin that proxies to the service, CORS does not apply. Server-side
callers ignore CORS entirely.

## Responses

| Status | Meaning                                   | Body                                          |
|--------|-------------------------------------------|-----------------------------------------------|
| `200`  | Accepted (email/Telegram sent async)      | `{"message": "Message received successfully!"}` |
| `400`  | Invalid/missing fields or bad email       | `{"error": "<reason>"}`                       |
| `403`  | `Referer` not an authorized domain        | `{"error": "Unauthorized domain"}`            |
| `413`  | Request body exceeded 64 KiB              | `{"error": "Payload too large"}`              |

A `200` means the submission was accepted and queued — the email and optional
Telegram notification are sent on background threads, so delivery failures are
logged server-side but are **not** reflected in the HTTP response.

## Examples

### Browser (same-origin or CORS-allowed origin)

```js
const res = await fetch("https://yourdomain.com/api/submit", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name, email, message }),
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
  -d '{"name":"Alice","email":"alice@example.com","message":"Hello"}'
```

```python
import requests

requests.post(
    "https://yourdomain.com/api/submit",
    json={"name": "Alice", "email": "alice@example.com", "message": "Hello"},
    headers={"Referer": "https://yourdomain.com/contact"},
    timeout=10,
).raise_for_status()
```

## Checklist for a working integration

1. The submitting page/script's domain is a key in `DOMAIN_EMAIL_MAP`.
2. Browser forms are served from an origin listed in `CORS_ORIGINS`.
3. Server-side callers set a `Referer` header on a configured domain.
4. Body is a JSON object with one or more scalar fields, within the limits
   above (include `email` if you want replies to reach the sender).
5. The public reverse proxy forwards to `127.0.0.1:8004`.
```
