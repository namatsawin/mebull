"""Run Alembic migrations in-process at startup.

Keeping this in-process means the container is self-contained: bringing the app up
applies any pending schema changes before the event loop starts. Idempotent — a
no-op when the DB is already at head (and when no versions exist yet, as in M0).
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from apm.config import get_settings
from apm.observability import get_logger

log = get_logger("migrate")

# repo root = .../src/apm/orchestrator/migrate.py -> parents[3]
_ROOT = Path(__file__).resolve().parents[3]


def _alembic_config() -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    # env.py drives an async engine with asyncpg — no separate sync driver needed.
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def run_migrations() -> None:
    log.info("migrations.upgrade.begin")
    command.upgrade(_alembic_config(), "head")
    log.info("migrations.upgrade.complete")
