# Trade Receiver

FastAPI alert ingest service with AI trade parsing, Creem subscription gating, and multi-broker execution.

## Quick start

```bash
cd trade-receiver
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Database defaults to `sqlite:///./data/trade.db` for local dev. For **Turso**, set:

```bash
DATABASE_URL=libsql://your-db-name-org.turso.io
TURSO_AUTH_TOKEN=your-token
```

The app converts `libsql://` to SQLAlchemy's `sqlite+libsql://` form automatically.

## Deploy on Coolify (Dockerfile)

Use the repo **Dockerfile** — do not use Nixpacks.

| Setting | Value |
|---------|--------|
| Service name | **Trade Receiver** |
| Repository | `fcpauldiaz/trade-receiver` |
| Build Pack | **Dockerfile** |
| Dockerfile location | `/Dockerfile` |
| Port | `8000` |
| Start command | leave empty (uses image `CMD`) |

Set production env vars (see `.env.example`). For Turso:

| Variable | Example |
|----------|---------|
| `DATABASE_URL` | `libsql://your-db-org.turso.io` |
| `TURSO_AUTH_TOKEN` | token from `turso db tokens create` |

No `/app/data` volume is required for remote Turso.

```bash
docker build -t trade-receiver .
docker run --rm -p 8000:8000 -e PORT=8000 -v trade-receiver-data:/app/data trade-receiver
```

## Environment

Copy `.env.example` to `.env`. Variables fall into three groups:

### Server secrets (required in production)

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | libSQL/SQLite connection (shared with Trade Desky auth tables) |
| `API_SECRET_KEY` | Signs OAuth state tokens |
| `ENCRYPTION_KEY` | Encrypts per-user broker tokens at rest |
| `RECEIVER_BASE_URL` | Public API URL (ingest + OAuth callbacks) |
| `PLATFORM_BASE_URL` | Where OAuth redirects after connect (e.g. `http://localhost:3000`) |
| `CREEM_API_KEY` | Creem API key (`creem_test_…` for sandbox) |
| `CREEM_WEBHOOK_SECRET` | Creem webhook HMAC secret |
| `CREEM_PRODUCT_ID` | Monthly Pro product ID for `POST /v1/me/billing/checkout` |
| `CREEM_YEARLY_PRODUCT_ID` | Yearly Pro product ID for checkout `{ "plan": "yearly" }` |
| `CREEM_SUCCESS_URL` | Optional checkout success redirect (defaults to `{PLATFORM_BASE_URL}/billing`) |
| `BETTER_AUTH_URL` | Public platform URL — JWT issuer/JWKS for API auth |
| `INTERNAL_API_SECRET` | Shared secret so signup can ensure a `subscriptions` row for the same `users` id |

### Creem setup

1. Sign up at [creem.io](https://creem.io) and copy a **test** API key (`creem_test_…`).
2. CLI: `creem login --api-key creem_test_YOUR_KEY` then `creem whoami`.
3. Create products:
   `creem products create --name "Pro Monthly" --description "Trade Desky Pro billed monthly" --price 3999 --currency USD --billing-type recurring --billing-period every-month --tax-category saas`
   `creem products create --name "Pro Yearly" --description "Trade Desky Pro billed yearly" --price 29900 --currency USD --billing-type recurring --billing-period every-year --tax-category saas`
4. Put `CREEM_API_KEY`, `CREEM_PRODUCT_ID`, `CREEM_YEARLY_PRODUCT_ID`, and `CREEM_WEBHOOK_SECRET` in `.env`.
5. Register webhook URL: `https://<receiver>/v1/webhooks/creem` for subscription + checkout events.
6. Start checkout from the authenticated app via `POST /v1/me/billing/checkout`.

### OAuth app registration (your developer apps — not user accounts)

Users connect brokers via the platform **Connections** page. These env vars register *your* app with each broker:

| Variable | Broker |
|----------|--------|
| `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`, `SCHWAB_REDIRECT_URI` | Schwab OAuth app |
| `TRADIER_CLIENT_ID`, `TRADIER_CLIENT_SECRET`, `TRADIER_REDIRECT_URI` | Tradier OAuth app |
| `TRADIER_API_BASE` | Sandbox vs live API host |

Per-user access tokens and account IDs are stored encrypted in `broker_connections` — never in `.env`.

### Optional

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | LLM alert parsing (falls back to rules if unset) |
| `TURSO_AUTH_TOKEN` | Remote libSQL auth (same value on Trade Desky) |
| `WEBULL_ENABLED` | Feature flag for Webull adapter |

## Broker connect flow

```mermaid
sequenceDiagram
    participant User
    participant Platform as Trade Desky
    participant Receiver as trade-receiver
    participant Broker

    User->>Platform: Click Connect Tradier/Schwab
    Platform->>Receiver: GET /v1/me/brokers/{broker}/authorize
    Receiver-->>Platform: OAuth URL with signed state
    Platform->>Broker: Redirect user to authorize
    Broker->>Receiver: Callback with code + state
    Receiver->>Broker: Exchange code for access token
    Receiver->>Receiver: Encrypt and store in broker_connections
    Receiver->>Platform: Redirect to /onboarding?broker={broker}
```

## API

- `POST /v1/internal/provision` — create/link user from Better Auth signup (internal secret)
- `POST /v1/internal/device-token` — issue desktop API key (internal secret)
- `GET /v1/me` — current user (Better Auth JWT or API key)
- `GET /v1/me/billing` — subscription status
- `POST /v1/me/billing/checkout` — create Creem checkout session (auth)
- `POST /v1/me/billing/portal` — Creem customer portal link (auth)
- `POST /v1/webhooks/creem` — Creem subscription webhooks
- `GET /v1/me/brokers/tradier/authorize` — start Tradier OAuth
- `GET /v1/me/brokers/schwab/authorize` — start Schwab OAuth
- `GET /v1/reviews` — public customer reviews (newest first)
- `GET /v1/me/review` — current user's review (auth)
- `POST /v1/me/reviews` — create or update review (active subscription required)
- `DELETE /v1/me/reviews` — remove own review (active subscription required)
- `GET /v1/me/settings` — trading prefs including sizing mode
- `PUT /v1/me/settings` — update paper/live, sizing, caps, tickers
- `POST /v1/me/onboarding/complete` — mark onboarding finished
- `POST /v1/me/brokers/{broker}/test-order` — place 1-share SPY test order (follows default_mode)
- `POST /v1/ingest` — authenticated alert ingest (desktop app Bearer token)

## Trade sizing

Users choose a sizing mode in settings or onboarding:

| Mode | Behavior |
|------|----------|
| `alert_inferred` | Use contract count from alert text (AI or rules), capped by `max_contracts` |
| `fixed` | Always trade `fixed_contracts` per alert |
| `risk_percent` | Size from account equity × `risk_percent` ÷ option cost, capped by `max_contracts` |

Sizing runs after option chain validation in the ingest pipeline.

## Migrations

Schema migrations run **automatically on app startup** (Alembic `upgrade head`). No manual step is required for deploy or local dev.

To create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
```

## Tests

```bash
DATABASE_URL=sqlite:///./data/test.db pytest
```

## Related repos

- [trade-desky](https://github.com/fcpauldiaz/trade-desky) — TanStack Start UI (marketing + logged-in app)
- [trade-desky-watcher](https://github.com/fcpauldiaz/trade-desky-watcher) — macOS/Windows desktop alert forwarder
