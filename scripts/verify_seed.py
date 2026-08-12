"""Prove the seeded data has the properties the rest of the project depends on.

This is not a smoke test for "did rows land". It checks the three claims that
later phases are built on, and fails loudly if any of them stops being true:

  1. The catalog is the right size and about a quarter of it is dead stock.
  2. There is enough price history per part for a 52-week seasonal model.
  3. Quote dispersion is dramatically wider inside shortage windows than
     outside them.

(3) is the one that matters most. Phase 2's volatility flag is a ratio of
current dispersion to baseline dispersion; if the seeded data does not actually
widen during spikes, the headline feature of the product measures noise.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ml-service"))

from app.config import get_settings, mongo_client, pg_connect  # noqa: E402

PASS = "  PASS"
FAIL = "  FAIL"


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        print(f"{PASS if ok else FAIL}  {label}{('  -- ' + detail) if detail else ''}")
        if not ok:
            self.failures += 1

    def info(self, label: str, detail: str) -> None:
        print(f"        {label:<34} {detail}")


def verify_catalog(checks: Checks) -> None:
    print("\ncatalog (mongo)")
    settings = get_settings()
    client = mongo_client()
    try:
        parts = client[settings.mongo_db]["parts"]
        total = parts.count_documents({})
        dead = parts.count_documents({"lifecycle_status": {"$in": ["Obsolete", "EOL"]}})
        dead_with_stock = parts.count_documents({
            "lifecycle_status": {"$in": ["Obsolete", "EOL"]},
            "authorized_stock": {"$gt": 0},
        })
        categories = len(parts.distinct("category"))
        manufacturers = len(parts.distinct("manufacturer"))
        defense = parts.count_documents({"is_defense_grade": True})

        checks.check(total >= 7900, "catalog size", f"{total:,} parts")
        checks.check(0.18 <= dead / total <= 0.32,
                     "obsolete/EOL share is roughly a quarter",
                     f"{dead / total:.1%}")
        # A part cannot be both discontinued and still stocked by the authorized
        # channel; that contradiction would poison the Phase 6 risk score.
        checks.check(dead_with_stock == 0,
                     "no obsolete part carries authorized stock",
                     f"{dead_with_stock} violations")
        checks.check(categories == 13, "categories", str(categories))
        checks.check(manufacturers >= 14, "manufacturers", str(manufacturers))
        checks.info("defense-grade parts", f"{defense:,} ({defense / total:.1%})")
    finally:
        client.close()


def verify_price_history(checks: Checks) -> None:
    print("\nprice history (postgres)")
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT part_mpn), "
                        "count(DISTINCT week_start) FROM parts_price_history")
            rows, parts, weeks = cur.fetchone()
            checks.check(rows > 2_000_000, "price rows", f"{rows:,}")
            checks.check(weeks >= 104, "distinct weeks", str(weeks))
            checks.info("parts with history", f"{parts:,}")
            checks.info("avg quotes per part-week", f"{rows / (parts * weeks):.2f}")

            # Every part needs >= 104 weeks or the SARIMA seasonal term (s=52)
            # has fewer than two cycles to fit.
            cur.execute("""
                SELECT count(*) FROM (
                    SELECT part_mpn FROM parts_price_history
                    GROUP BY part_mpn HAVING count(DISTINCT week_start) < 104
                ) short
            """)
            short = cur.fetchone()[0]
            checks.check(short == 0,
                         "every part has a full 104-week series",
                         f"{short} short series")
    finally:
        conn.close()


# This reproduces the Phase 2 heat index, so it checks two things at once: that
# the shortage events are present, and that the thresholds Phase 2 will use are
# calibrated to this data.
#
#   dispersion_ratio = (P75 - P25) / P50   per part-week
#   smoothed         = mean over the trailing 8 weeks
#   baseline         = that part's own median dispersion
#   heat_index       = smoothed / baseline        (> 2.0 is VOLATILE)
#
# The 8-week smoothing is not cosmetic. A single week holds only ~3 quotes, so
# one week's P25-P75 is extremely noisy; the maximum of 104 such weeks exceeds
# twice the median for essentially every part, purely as an extreme-value
# artefact. Smoothing first is what separates a real shortage from sampling
# noise -- and is exactly why Phase 2 specifies a trailing 8-week window.
DISPERSION_SQL = """
WITH weekly AS (
    SELECT part_mpn,
           week_start,
           (percentile_cont(0.75) WITHIN GROUP (ORDER BY quoted_price)
            - percentile_cont(0.25) WITHIN GROUP (ORDER BY quoted_price))
           / NULLIF(percentile_cont(0.50) WITHIN GROUP (ORDER BY quoted_price), 0)
             AS dispersion_ratio
    FROM parts_price_history
    GROUP BY part_mpn, week_start
    HAVING count(*) >= 3
),
smoothed AS (
    SELECT part_mpn,
           week_start,
           avg(dispersion_ratio) OVER (
               PARTITION BY part_mpn ORDER BY week_start
               ROWS BETWEEN 7 PRECEDING AND CURRENT ROW
           ) AS smoothed_ratio,
           dispersion_ratio
    FROM weekly
),
baselines AS (
    SELECT part_mpn,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY dispersion_ratio) AS baseline
    FROM smoothed
    GROUP BY part_mpn
),
heat AS (
    SELECT s.part_mpn,
           max(s.smoothed_ratio / NULLIF(b.baseline, 0)) AS peak_heat_index,
           b.baseline
    FROM smoothed s
    JOIN baselines b USING (part_mpn)
    GROUP BY s.part_mpn, b.baseline
)
SELECT count(*)                                                  AS parts,
       count(*) FILTER (WHERE peak_heat_index > 2.0)             AS volatile_parts,
       count(*) FILTER (WHERE peak_heat_index BETWEEN 1.3 AND 2.0) AS elevated_parts,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY baseline)     AS typical_baseline,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY peak_heat_index) AS typical_peak_heat
FROM heat
"""

# The same comparison at the row level: weeks where a lot of brokers quoted
# (which only happens during a shortage) against ordinary weeks.
BUSY_WEEK_SQL = """
WITH weekly AS (
    SELECT part_mpn,
           count(*) AS quotes,
           (percentile_cont(0.75) WITHIN GROUP (ORDER BY quoted_price)
            - percentile_cont(0.25) WITHIN GROUP (ORDER BY quoted_price))
           / NULLIF(percentile_cont(0.50) WITHIN GROUP (ORDER BY quoted_price), 0)
             AS dispersion_ratio,
           avg(lead_time_days) AS lead_time
    FROM parts_price_history
    GROUP BY part_mpn, week_start
    HAVING count(*) >= 3
)
SELECT
    percentile_cont(0.5) WITHIN GROUP (ORDER BY dispersion_ratio)
        FILTER (WHERE quotes <= 4) AS calm_dispersion,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY dispersion_ratio)
        FILTER (WHERE quotes >= 6) AS busy_dispersion,
    avg(lead_time) FILTER (WHERE quotes <= 4) AS calm_lead,
    avg(lead_time) FILTER (WHERE quotes >= 6) AS busy_lead,
    count(*) FILTER (WHERE quotes >= 6) AS busy_weeks
FROM weekly
"""


def verify_dispersion(checks: Checks) -> None:
    print("\nshortage signal (the property Phase 2 depends on)")
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(DISPERSION_SQL)
            parts, hot, warm, baseline, peak_heat = cur.fetchone()
            share = hot / parts if parts else 0
            checks.info("parts analysed", f"{parts:,}")
            checks.info("typical baseline dispersion", f"{baseline:.4f}")
            checks.info("median peak heat index", f"{peak_heat:.2f}")
            checks.info("would flag ELEVATED (1.3-2.0)", f"{warm:,}")
            # Wider than the 18% injection rate on purpose: a part's PEAK heat
            # over 104 weeks can clear 2.0 on noise alone, so the share that
            # would ever flag is legitimately higher than the share that had a
            # shortage injected. The label states the band being tested rather
            # than a single figure the check does not actually enforce.
            checks.check(
                0.08 <= share <= 0.30,
                "parts whose PEAK heat clears 2.0 is within 8-30%",
                f"{share:.1%} ({hot:,} of {parts:,}); ~18% had a shortage injected",
            )

            cur.execute(BUSY_WEEK_SQL)
            calm_d, busy_d, calm_lead, busy_lead, busy_weeks = cur.fetchone()
            ratio = float(busy_d) / float(calm_d) if calm_d else 0.0
            checks.info("calm-week dispersion (<=4 quotes)", f"{calm_d:.4f}")
            checks.info("busy-week dispersion (>=6 quotes)", f"{busy_d:.4f}")
            checks.info("busy weeks in the data", f"{busy_weeks:,}")
            checks.check(
                ratio >= 2.0,
                "shortage weeks are visibly more dispersed",
                f"{ratio:.2f}x wider",
            )
            checks.info("avg lead time calm / busy",
                        f"{calm_lead:.0f}d / {busy_lead:.0f}d")
    finally:
        conn.close()


def main() -> int:
    print("verifying seeded data")
    checks = Checks()
    verify_catalog(checks)
    verify_price_history(checks)
    verify_dispersion(checks)

    print()
    if checks.failures:
        print(f"{checks.failures} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
