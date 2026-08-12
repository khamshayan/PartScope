"""Seed both datastores from a clean slate.

  1. apply the Postgres migrations
  2. generate the parts catalog into Mongo
  3. generate the price history into Postgres

Idempotent: running it twice produces exactly the same data, because everything
derives from SEED_RANDOM_SEED. It refuses to clobber an existing seed unless
--force is passed, so nobody wipes three minutes of work by hitting up-arrow.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ml-service"))
sys.path.insert(0, str(REPO_ROOT / "ml-service" / "data"))

from app.config import get_settings, mongo_client, pg_connect  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "api" / "src" / "db" / "migrations"


def apply_migrations() -> list[str]:
    """Run every .sql file in order. They are all CREATE ... IF NOT EXISTS, so
    re-running is safe; a real migration ledger arrives with the Node API."""
    applied = []
    conn = pg_connect()
    try:
        with conn, conn.cursor() as cur:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
    finally:
        conn.close()
    return applied


def existing_counts() -> tuple[int, int]:
    parts = 0
    prices = 0
    client = mongo_client()
    try:
        parts = client[get_settings().mongo_db]["parts"].estimated_document_count()
    except Exception:  # noqa: BLE001 - a missing collection is just zero
        parts = 0
    finally:
        client.close()

    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.parts_price_history')")
            if cur.fetchone()[0] is not None:
                cur.execute("SELECT count(*) FROM parts_price_history")
                prices = cur.fetchone()[0]
    finally:
        conn.close()
    return parts, prices


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Seed Source Scope's datastores")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing seeded data")
    parser.add_argument("--count", type=int, default=settings.catalog_size)
    parser.add_argument("--weeks", type=int, default=settings.history_weeks)
    args = parser.parse_args()

    started = time.perf_counter()

    print("[1/3] applying migrations")
    for name in apply_migrations():
        print(f"    {name}")

    parts_count, price_count = existing_counts()
    if (parts_count or price_count) and not args.force:
        print(f"\nalready seeded: {parts_count:,} parts, {price_count:,} price rows")
        print("nothing to do. use --force (or `make reseed`) to regenerate.")
        return 0

    from generate_catalog import generate_parts, summarise, write_to_mongo
    from generate_price_history import write_to_postgres

    print(f"\n[2/3] catalog: {args.count} parts -> mongo")
    parts = generate_parts(args.count, settings.seed)
    written = write_to_mongo(parts)
    print(summarise(parts))
    print(f"    wrote {written:,} documents")

    print(f"\n[3/3] price history: {args.weeks} weeks -> postgres")
    rows = write_to_postgres(parts, args.weeks, settings.seed)
    print(f"    wrote {rows:,} rows")

    elapsed = time.perf_counter() - started
    print(f"\nseeded in {elapsed:.1f}s")
    print("run `make verify` to confirm the data has the properties we claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
