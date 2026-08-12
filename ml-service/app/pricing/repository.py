"""Postgres access for price history.

All aggregation happens in the database. Pulling 350 raw quote rows per part
into Python to compute a percentile would work at this scale, but it stops
working the moment the history is real, and `percentile_cont` over the
(part_mpn, week_start) index is what the relational store is here for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from app.config import pg_connect

# Weekly aggregate per part. One row per week, ordered oldest first.
WEEKLY_SQL = """
    SELECT part_mpn,
           week_start,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY quoted_price) AS p25,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY quoted_price) AS p50,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY quoted_price) AS p75,
           count(*)                                                  AS quotes,
           avg(lead_time_days)                                       AS lead_time
    FROM parts_price_history
    WHERE part_mpn = ANY(%(mpns)s)
    GROUP BY part_mpn, week_start
    ORDER BY part_mpn, week_start
"""

# Price band: percentiles over every quote in the trailing window, pooled.
# Pooling raw quotes is right for the band -- it answers "what will I actually
# pay over the next couple of months" -- but see volatility.py for why the heat
# index must NOT be computed this way.
BAND_SQL = """
    WITH recent AS (
        SELECT quoted_price
        FROM parts_price_history
        WHERE part_mpn = %(mpn)s
          AND week_start > (
              SELECT max(week_start) - (%(weeks)s || ' weeks')::interval
              FROM parts_price_history WHERE part_mpn = %(mpn)s
          )
    )
    SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY quoted_price),
           percentile_cont(0.50) WITHIN GROUP (ORDER BY quoted_price),
           percentile_cont(0.75) WITHIN GROUP (ORDER BY quoted_price),
           count(*)
    FROM recent
"""


@dataclass(frozen=True)
class WeeklyPoint:
    week_start: date
    p25: float
    p50: float
    p75: float
    quotes: int
    lead_time_days: float

    @property
    def dispersion_ratio(self) -> float:
        """(P75 - P25) / P50 for this week. Zero when the median is degenerate."""
        return (self.p75 - self.p25) / self.p50 if self.p50 > 0 else 0.0


@dataclass
class PriceSeries:
    """One part's full weekly history, oldest first."""

    mpn: str
    points: list[WeeklyPoint]

    def __len__(self) -> int:
        return len(self.points)

    @property
    def medians(self) -> np.ndarray:
        return np.array([p.p50 for p in self.points], dtype=float)

    @property
    def dispersions(self) -> np.ndarray:
        return np.array([p.dispersion_ratio for p in self.points], dtype=float)

    @property
    def weeks(self) -> list[date]:
        return [p.week_start for p in self.points]

    @property
    def last_median(self) -> float:
        return self.points[-1].p50 if self.points else 0.0

    def head(self, n: int) -> "PriceSeries":
        """First n weeks -- the training window in a walk-forward split."""
        return PriceSeries(self.mpn, self.points[:n])


def fetch_series(mpns: list[str]) -> dict[str, PriceSeries]:
    """Weekly aggregates for many parts in one round trip."""
    if not mpns:
        return {}
    grouped: dict[str, list[WeeklyPoint]] = {mpn: [] for mpn in mpns}
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(WEEKLY_SQL, {"mpns": list(mpns)})
            for mpn, week, p25, p50, p75, quotes, lead in cur:
                grouped.setdefault(mpn, []).append(
                    WeeklyPoint(week, float(p25), float(p50), float(p75),
                                int(quotes), float(lead))
                )
    finally:
        conn.close()
    return {mpn: PriceSeries(mpn, points) for mpn, points in grouped.items()}


def fetch_one_series(mpn: str) -> PriceSeries:
    return fetch_series([mpn]).get(mpn, PriceSeries(mpn, []))


def fetch_band(mpn: str, weeks: int = 8) -> tuple[float, float, float, int]:
    """Pooled P25/P50/P75 and quote count over the trailing `weeks` weeks."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(BAND_SQL, {"mpn": mpn, "weeks": weeks})
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or row[3] == 0:
        return 0.0, 0.0, 0.0, 0
    return float(row[0]), float(row[1]), float(row[2]), int(row[3])


def stratified_sample(limit: int = 500, seed: int = 20240815) -> list[str]:
    """A backtest sample spanning the segments the results are broken out by.

    Deliberately not a plain random draw: obsolete parts and spiked parts are
    where the interesting forecasting behaviour is, and a uniform sample of a
    catalog that is 77% active would under-represent them.
    """
    from app.config import get_settings, mongo_client

    client = mongo_client()
    try:
        parts = list(client[get_settings().mongo_db]["parts"].find(
            {}, {"_id": 0, "mpn": 1, "lifecycle_status": 1, "category": 1}
        ))
    finally:
        client.close()

    import random

    rng = random.Random(seed)
    dead = [p["mpn"] for p in parts if p["lifecycle_status"] in ("Obsolete", "EOL")]
    alive = [p["mpn"] for p in parts if p["lifecycle_status"] not in ("Obsolete", "EOL")]
    rng.shuffle(dead)
    rng.shuffle(alive)

    # Half and half, so neither segment's error is estimated from a handful of
    # parts. The catalog's true split is reported separately in the write-up.
    half = limit // 2
    chosen = dead[:half] + alive[: limit - min(half, len(dead))]
    rng.shuffle(chosen)
    return chosen[:limit]
