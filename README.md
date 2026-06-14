# Trade Receiver

FastAPI webhook receiver with AI trade parsing, Lemon Squeezy subscription gating, and multi-broker execution.

## Quick start

```bash
cd trade-receiver
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Database defaults to `sqlite+libsql:///./data/trade.db`.

## Environment

See `.env.example`.

## API

- `POST /v1/users` — register
- `GET /v1/me` — current user
- `GET /v1/me/billing` — subscription status
- `POST /hooks/{user_id}/{secret}` — notification webhook
## Migrations

```bash
alembic upgrade head
```

## Tests

```bash
DATABASE_URL=sqlite:///./data/test.db pytest
```

## Related repos

- [trade-platform](https://github.com/) — TanStack Start UI
- [notification-watcher](https://github.com/) — macOS/Windows webhook sender
