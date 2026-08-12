"""Generate synthetic secondary-market price history and load it into Postgres.

THE DATA IS SYNTHETIC. See docs/data-sources.md.

What this has to produce, and why:

* A weekly median series with trend and a mild annual season, so the SARIMA
  model in Phase 2 has real structure to find rather than noise.
* Shortage events -- a sudden 2x-5x spike that decays over 8-20 weeks -- because
  that is the market behaviour the whole product is about.
* Several independent broker quotes per part per week, whose SPREAD widens
  sharply during a shortage. This is the single most important property in the
  file. The Phase 2 volatility flag reads dispersion, not price level: when
  brokers stop agreeing with each other, the market is heating up. If the
  dispersion did not widen during spikes the headline feature would be
  measuring nothing.

Volume: ~8,000 parts x 104 weeks x ~3 quotes = ~2.6M rows, written with a
single COPY stream. Row-by-row INSERT of that many rows takes tens of minutes;
COPY takes tens of seconds.

Run standalone:  python ml-service/data/generate_price_history.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings, pg_connect  # noqa: E402

# The history ends the week before the catalog epoch, so "next week" -- the
# thing the forecaster predicts -- is a well-defined point just past the data.
HISTORY_END = date(2024, 12, 30)

# Base price distributions, as (mu, sigma) of a log-normal in dollars. A reel of
# 0402 resistors is fractions of a cent apiece; a radiation-tolerant FPGA is
# four figures. Spanning that range is what makes the price band column
# interesting to look at.
PRICE_TIERS = {
    "jellybean": (-4.0, 0.7),   # ~$0.02 median
    "low": (-1.4, 0.8),         # ~$0.25
    "mid": (0.6, 0.9),          # ~$1.80
    "high": (2.3, 1.0),         # ~$10
    "premium": (5.2, 1.1),      # ~$180
}

# Share of parts that get a shortage event at all. Obsolete parts are far more
# exposed: there is no authorized supply to absorb a demand shock.
SHORTAGE_PROB_ACTIVE = 0.11
SHORTAGE_PROB_DEAD = 0.42

BROKER_POOL = [f"BRK-{i:03d}" for i in range(1, 61)]


def _week_starts(weeks: int) -> list[date]:
    """Monday-aligned week starts, oldest first, ending at HISTORY_END."""
    return [HISTORY_END - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]


def _base_price(rng: random.Random, part: dict) -> float:
    mu, sigma = PRICE_TIERS[part.get("price_tier", "mid")]
    price = math.exp(rng.gauss(mu, sigma))
    # Obsolete parts already trade at a premium before any shortage: the
    # authorized channel is gone, so you are paying a broker's margin.
    if part["lifecycle_status"] in ("Obsolete", "EOL"):
        price *= rng.uniform(1.4, 3.2)
    elif part["lifecycle_status"] == "NRND":
        price *= rng.uniform(1.05, 1.5)
    return max(0.004, price)


def _median_series(rng: random.Random, np_rng: np.random.Generator,
                   part: dict, weeks: int) -> tuple[np.ndarray, np.ndarray]:
    """Weekly median price, and a per-week 0..1 'shortage intensity'.

    Returns (prices, intensity). Intensity is what widens the broker spread.
    """
    t = np.arange(weeks)
    base = _base_price(rng, part)

    # Slow trend. Obsolete parts drift up as remaining stock is consumed;
    # active parts drift gently down as processes mature.
    is_dead = part["lifecycle_status"] in ("Obsolete", "EOL")
    annual_drift = rng.uniform(0.04, 0.30) if is_dead else rng.uniform(-0.10, 0.05)
    trend = (1.0 + annual_drift) ** (t / 52.0)

    # Mild annual season: procurement budgets and factory shutdowns are real,
    # but they are a few percent, not a doubling. Random phase per part.
    amplitude = rng.uniform(0.015, 0.06)
    phase = rng.uniform(0, 2 * np.pi)
    season = 1.0 + amplitude * np.sin(2 * np.pi * t / 52.0 + phase)

    # Random-walk wander plus white noise, so the series is not a clean curve.
    wander = np.cumsum(np_rng.normal(0.0, 0.012, weeks))
    noise = np_rng.normal(0.0, 0.02, weeks)

    prices = base * trend * season * np.exp(wander + noise)

    # ---- shortage events -------------------------------------------------
    intensity = np.zeros(weeks)
    p_shortage = SHORTAGE_PROB_DEAD if is_dead else SHORTAGE_PROB_ACTIVE
    if rng.random() < p_shortage:
        # Leave room at both ends so the spike and its decay are both visible.
        onset = rng.randint(12, max(13, weeks - 24))
        magnitude = rng.uniform(3.0, 6.0) if is_dead else rng.uniform(2.0, 5.0)
        decay_weeks = rng.randint(8, 20)

        ramp = np.arange(weeks) - onset
        active = ramp >= 0
        # Exponential decay from the peak back toward the pre-spike level.
        shape = np.zeros(weeks)
        shape[active] = np.exp(-ramp[active] / decay_weeks)
        prices = prices * (1.0 + (magnitude - 1.0) * shape)
        intensity = shape

    return prices, intensity


def _quotes_for_part(part: dict, weeks: list[date], seed: int) -> list[tuple]:
    """Build every broker quote row for one part."""
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    n = len(weeks)

    prices, intensity = _median_series(rng, np_rng, part, n)

    # A stable part has a handful of brokers who roughly agree. Base spread is
    # a few percent.
    base_sigma = rng.uniform(0.05, 0.10)
    lead_base = rng.randint(2, 12) * 7 if part["authorized_stock"] == 0 else rng.randint(1, 4) * 7

    broker_count = rng.randint(4, 9)
    brokers = rng.sample(BROKER_POOL, broker_count)

    rows: list[tuple] = []
    for i, week in enumerate(weeks):
        heat = float(intensity[i])

        # THE KEY LINE. Quote dispersion scales with shortage intensity: at the
        # peak of an event the spread is roughly five times its calm width.
        # Brokers price on rumour and on whatever they personally hold, so they
        # stop agreeing long before the median has finished moving.
        sigma = base_sigma * (1.0 + 4.0 * heat)

        # Three brokers on a calm week, up to eight when a shortage draws them
        # in. Averages ~3.3 quotes/part/week across the catalog.
        n_quotes = 3 + int(round(4.0 * heat)) + (1 if rng.random() < 0.35 else 0)
        n_quotes = min(n_quotes, broker_count)

        median = float(prices[i])
        for broker in rng.sample(brokers, n_quotes):
            quoted = median * float(np_rng.lognormal(0.0, sigma))
            qty = int(np_rng.lognormal(6.5, 1.5))
            # Lead times stretch when the market is tight -- the same signal in
            # a different units.
            lead = int(lead_base * (1.0 + 1.8 * heat) + np_rng.normal(0, 3))
            rows.append((
                part["mpn"],
                week.isoformat(),
                broker,
                round(max(0.0008, quoted), 4),
                max(1, qty),
                max(1, min(365, lead)),
            ))
    return rows


# ---------------------------------------------------------------------------
# Postgres load
# ---------------------------------------------------------------------------

COPY_SQL = """
    COPY parts_price_history
        (part_mpn, week_start, broker_id, quoted_price, quoted_qty, lead_time_days)
    FROM STDIN WITH (FORMAT csv)
"""


def _flush(cursor, buffer: io.StringIO) -> None:
    buffer.seek(0)
    cursor.copy_expert(COPY_SQL, buffer)


def write_to_postgres(parts: list[dict], weeks: int, seed: int,
                      batch_parts: int = 400, progress=print) -> int:
    """Stream every part's quotes into Postgres with COPY.

    Indexes are dropped first and rebuilt afterwards: maintaining a btree while
    2.6M rows stream in roughly doubles the load time for no benefit, since
    nothing reads the table until the load finishes.
    """
    week_list = _week_starts(weeks)
    conn = pg_connect()
    total = 0
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("DROP INDEX IF EXISTS idx_price_history_mpn_week")
            cur.execute("DROP INDEX IF EXISTS idx_price_history_week")
            cur.execute("TRUNCATE parts_price_history")

            buffer = io.StringIO()
            # csv.writer, not f-string concatenation: some manufacturers really
            # do put commas in part numbers (Nexperia ships 74HC595D,118), and
            # an unquoted comma silently shifts every column of that row.
            writer = csv.writer(buffer, lineterminator="\n")
            for offset in range(0, len(parts), batch_parts):
                chunk = parts[offset:offset + batch_parts]
                for index, part in enumerate(chunk):
                    # Per-part seed keeps each part's series reproducible on its
                    # own, independent of how the work is batched.
                    part_seed = seed + offset + index
                    rows = _quotes_for_part(part, week_list, part_seed)
                    writer.writerows(rows)
                    total += len(rows)
                _flush(cur, buffer)
                buffer.close()
                buffer = io.StringIO()
                writer = csv.writer(buffer, lineterminator="\n")
                progress(f"    {min(offset + batch_parts, len(parts))}/{len(parts)} parts "
                         f"-> {total:,} rows")
            buffer.close()

            progress("    rebuilding indexes...")
            cur.execute("CREATE INDEX idx_price_history_mpn_week "
                        "ON parts_price_history (part_mpn, week_start)")
            cur.execute("CREATE INDEX idx_price_history_week "
                        "ON parts_price_history (week_start)")
            cur.execute("ANALYZE parts_price_history")
        conn.commit()
        return total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Generate synthetic price history")
    parser.add_argument("--weeks", type=int, default=settings.history_weeks)
    parser.add_argument("--seed", type=int, default=settings.seed)
    parser.add_argument("--count", type=int, default=settings.catalog_size,
                        help="parts to generate history for (dry-run only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate in memory and report statistics only")
    args = parser.parse_args()

    if args.dry_run:
        from generate_catalog import generate_parts
        parts = generate_parts(args.count, args.seed)
    else:
        from app.config import mongo_client
        client = mongo_client()
        try:
            parts = list(client[settings.mongo_db]["parts"].find(
                {}, {"_id": 0, "mpn": 1, "lifecycle_status": 1,
                     "authorized_stock": 1, "price_tier": 1}))
        finally:
            client.close()
        if not parts:
            print("no parts in mongo -- run generate_catalog.py first")
            return 1

    print(f"generating {args.weeks} weeks of history for {len(parts)} parts "
          f"(seed={args.seed})...")
    started = time.perf_counter()

    if args.dry_run:
        week_list = _week_starts(args.weeks)
        sample = parts[:400]
        rows = 0
        for i, part in enumerate(sample):
            rows += len(_quotes_for_part(part, week_list, args.seed + i))
        per_part = rows / len(sample)
        print(f"  {rows:,} rows from {len(sample)} parts "
              f"({per_part:.1f} per part, {per_part / args.weeks:.2f} quotes/week)")
        print(f"  projected total: {int(per_part * len(parts)):,} rows")
        print(f"  elapsed {time.perf_counter() - started:.1f}s for the sample")
        return 0

    total = write_to_postgres(parts, args.weeks, args.seed)
    print(f"wrote {total:,} price rows in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
