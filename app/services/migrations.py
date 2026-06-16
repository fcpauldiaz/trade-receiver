from pathlib import Path

from alembic import command
from alembic.config import Config

from app.database import normalized_database_url


def run_migrations() -> None:
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", normalized_database_url())
    command.upgrade(cfg, "head")
