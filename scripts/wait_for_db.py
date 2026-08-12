"""Block until Postgres and Mongo are actually accepting connections.

`docker compose up -d` returns as soon as the containers start, which is well
before Postgres has finished its first-run initialisation. Seeding immediately
after gives a confusing connection-refused error, so wait properly.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml-service"))

from app.config import get_settings  # noqa: E402

TIMEOUT_SECONDS = 90
POLL_SECONDS = 1.5


def _wait(name: str, probe, deadline: float) -> bool:
    last_error = None
    while time.time() < deadline:
        try:
            probe()
            print(f"  {name:<10} ready")
            return True
        except Exception as exc:  # noqa: BLE001 - any failure means not ready yet
            last_error = exc
            time.sleep(POLL_SECONDS)
    print(f"  {name:<10} TIMED OUT after {TIMEOUT_SECONDS}s: {last_error}")
    return False


def _probe_postgres() -> None:
    import psycopg2

    settings = get_settings()
    conn = psycopg2.connect(settings.pg_dsn, connect_timeout=3)
    conn.close()


def _probe_mongo() -> None:
    from pymongo import MongoClient

    settings = get_settings()
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    finally:
        client.close()


def main() -> int:
    settings = get_settings()
    print(f"waiting for databases "
          f"(postgres :{settings.pg_port}, mongo {settings.mongo_uri})")
    deadline = time.time() + TIMEOUT_SECONDS
    ok = _wait("postgres", _probe_postgres, deadline)
    ok = _wait("mongo", _probe_mongo, deadline) and ok
    if not ok:
        print("\nAre the containers up?  docker compose up -d")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
