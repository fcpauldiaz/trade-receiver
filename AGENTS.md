# AGENTS.md

## Cursor Cloud specific instructions

`trade-receiver` is a single Python 3.12 FastAPI service (no monorepo, no
frontend). Standard setup/run/test commands live in `README.md`; the notes
below only cover Cloud-specific, non-obvious details.

### Environment
- Dependencies are installed into a virtualenv at `.venv` (kept out of git). The
  startup update script recreates/updates it via `pip install -e ".[dev]"`.
  Activate it before running anything: `source .venv/bin/activate`.
- System `python3` is 3.12; `python3-venv` is required to create the venv and is
  installed at the system level (not part of the update script).

### Running the service
- Local Postgres: `docker compose up -d db` (image `postgres:18`,
  `postgresql://trade:trade@localhost:5432/trade`).
- Dev server: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
  (from repo root, venv activated). It listens on port `8000`.
- No `.env` is required for local dev if Postgres is on localhost with the
  default URL (`INTERNAL_API_SECRET=dev-internal-secret`).
- Alembic migrations run automatically on app startup (`upgrade head`); there is
  no manual migration step for local dev. Current head is `003` (`users.role` for
  admin access; `002` is `ai_evaluations`; `001` is the PostgreSQL baseline).
- Super admin: set `users.role = 'admin'` (default is `user`). Then `/v1/admin/*`
  accepts that user's Better Auth JWT. Bootstrap example:
  `UPDATE users SET role = 'admin' WHERE email = 'you@example.com';`
- `GET /health` returns DB status and the current migration head — use it as a
  readiness check.

### Tests
- Run against Postgres 18: `DATABASE_URL=postgresql://trade:trade@localhost:5432/trade pytest`
  (matches CI in `.github/workflows/ci.yml`). All external services
  (OpenAI, Vercel AI Gateway, brokers, Better Auth) are mocked in the suite.

### External dependencies (not needed to boot or test)
- OpenAI / Vercel AI Gateway, Tradier/Schwab/Webull/NinjaTrader brokers, and the
  `trade-platform` Better Auth issuer are external network services. They are
  optional for running the app and the test suite; only a full production
  auth→connect→ingest→execute flow needs real credentials.

### Quick end-to-end smoke test (no external services)
Provision a user with the internal secret, mint a device API key, then call an
authenticated endpoint:
```bash
curl -s -X POST localhost:8000/v1/internal/provision \
 -H "X-Internal-Secret: dev-internal-secret" -H "Content-Type: application/json" \
 -d '{"auth_id":"a1","email":"demo@example.com","name":"Demo"}'
# mint key
curl -s -X POST localhost:8000/v1/internal/device-token \
 -H "X-Internal-Secret: dev-internal-secret" -H "Content-Type: application/json" \
 -d '{"auth_id":"a1"}'
# use returned api_key
curl -s localhost:8000/v1/me -H "Authorization: Bearer <api_key>"
```
- `PUT /v1/me/settings` requires the full settings object (all fields), and
  `allowed_tickers` is a comma-separated string (e.g. `"SPY,QQQ"`), not a list.
