"""Single source of truth for configuration and database handles.

Loaded by the FastAPI service, the data generators and the seed scripts alike,
so there is exactly one place that knows how to reach Postgres and Mongo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# repo root: ml-service/app/config.py -> ml-service/app -> ml-service -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]
ML_SERVICE_ROOT = REPO_ROOT / "ml-service"
DATA_DIR = ML_SERVICE_ROOT / "data"

# A missing .env is not an error: every value below has a default that matches
# what docker-compose.yml brings up, so a clean clone runs without any setup.
load_dotenv(REPO_ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str

    mongo_uri: str
    mongo_db: str

    ml_service_url: str

    seed: int
    catalog_size: int
    history_weeks: int

    nexar_api_key: str
    mouser_api_key: str

    @property
    def pg_dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        pg_host=os.getenv("POSTGRES_HOST", "localhost"),
        pg_port=_int("POSTGRES_PORT", 5433),
        pg_db=os.getenv("POSTGRES_DB", "partscope"),
        pg_user=os.getenv("POSTGRES_USER", "partscope"),
        pg_password=os.getenv("POSTGRES_PASSWORD", "partscope"),
        mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27018"),
        mongo_db=os.getenv("MONGO_DB", "partscope"),
        ml_service_url=os.getenv("ML_SERVICE_URL", "http://localhost:8000"),
        seed=_int("SEED_RANDOM_SEED", 20240815),
        catalog_size=_int("CATALOG_SIZE", 8000),
        history_weeks=_int("PRICE_HISTORY_WEEKS", 104),
        nexar_api_key=os.getenv("NEXAR_API_KEY", "").strip(),
        mouser_api_key=os.getenv("MOUSER_API_KEY", "").strip(),
    )


def pg_connect():
    """Open a psycopg2 connection. Caller owns closing it."""
    import psycopg2

    return psycopg2.connect(get_settings().pg_dsn)


def mongo_client():
    """Open a pymongo client. Caller owns closing it."""
    from pymongo import MongoClient

    return MongoClient(get_settings().mongo_uri, serverSelectionTimeoutMS=5000)


def mongo_db():
    settings = get_settings()
    return mongo_client()[settings.mongo_db]
