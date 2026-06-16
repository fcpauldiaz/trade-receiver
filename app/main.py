from contextlib import asynccontextmanager

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import billing, brokers, ingest, internal, reviews, settings as settings_api, trades, users
from app.config import settings as app_settings
from app.database import engine
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.migrations import run_migrations
from app.services.production import validate_production_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_production_settings()
    run_migrations()
    yield


app = FastAPI(title="Trade Receiver", version="0.1.0", lifespan=lifespan)

cors_origins = [app_settings.platform_base_url.rstrip("/")]
if app_settings.cors_extra_origins:
    cors_origins.extend([o.strip() for o in app_settings.cors_extra_origins.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(ingest.router)
app.include_router(internal.router)
app.include_router(users.router)
app.include_router(billing.router)
app.include_router(brokers.router)
app.include_router(settings_api.router)
app.include_router(trades.router)
app.include_router(reviews.router)


def _migration_head() -> str | None:
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head()


@app.get("/health")
def health():
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "migration_head": _migration_head(),
    }
