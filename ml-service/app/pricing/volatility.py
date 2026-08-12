"""Market heat: how far broker quote dispersion has moved from its own baseline.

This is the headline feature, so it is worth being precise about what it
measures and why it is built this way.

WHAT IT MEASURES

Not price level. Not price change. *Disagreement between brokers.*

In a calm market the handful of brokers holding a part quote within a few
percent of each other, because they can all see roughly the same supply. When a
shortage starts, they stop agreeing: one broker holding real stock quotes 4x,
another with none quotes near the old price to stay on the bid list, a third
guesses. That spread widens before and more sharply than the median moves, which
is what makes it worth showing a buyer.

    dispersion_ratio = (P75 - P25) / P50     per week
    current          = mean dispersion_ratio over the trailing 8 weeks
    baseline         = median dispersion_ratio over the trailing 52 weeks
    heat_index       = current / baseline

    heat < 1.3   STABLE     (green)
    1.3 - 2.0    ELEVATED   (amber)
    > 2.0        VOLATILE   (red)

It is a ratio against the part's OWN history, not an absolute threshold. A
jellybean resistor quoted by eight brokers within 2% and an obsolete FPGA quoted
by three within 30% are both "normal" for what they are; heat only fires when a
part departs from its own habits.

TWO DELIBERATE CHOICES

1. Dispersion is computed per week and then averaged over the 8-week window --
   NOT by pooling all quotes from those 8 weeks into one percentile.

   Pooling looks simpler and is wrong. If a part's price triples over eight
   weeks, the pooled P25-P75 spread is enormous even if every broker agreed
   perfectly every single week, because the spread now contains the trend. That
   would flag a smooth, predictable price rise as "brokers disagree", which is
   precisely the opposite of the signal we want. Measuring within a week and
   then averaging isolates disagreement from drift.

   (The price *band* shown in the UI is still pooled over the 8 weeks -- there
   it is correct, because the question is "what will I pay", and the trend
   genuinely is part of that answer. See repository.BAND_SQL.)

2. The 8-week window is smoothing, not decoration. A single week holds only
   about three quotes, so one week's P25-P75 is dominated by sampling noise; the
   maximum of 104 such weeks clears twice the median for essentially every part
   in the catalog. Smoothing first is what separates a shortage from noise, as
   scripts/verify_seed.py demonstrates against the seeded data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .repository import PriceSeries

STABLE = "STABLE"
ELEVATED = "ELEVATED"
VOLATILE = "VOLATILE"

ELEVATED_THRESHOLD = 1.3
VOLATILE_THRESHOLD = 2.0

CURRENT_WINDOW_WEEKS = 8
BASELINE_WINDOW_WEEKS = 52
SPARKLINE_WEEKS = 52

# Below this the ratio is numerically meaningless -- a part whose brokers agreed
# to the cent has a near-zero baseline, and dividing by it manufactures enormous
# heat from nothing.
MIN_BASELINE = 1e-4


@dataclass
class VolatilityAssessment:
    heat_index: float
    flag: str
    dispersion_ratio: float
    baseline_dispersion: float
    weeks_observed: int
    sparkline: list[float]

    def to_dict(self) -> dict:
        return {
            "heat_index": round(self.heat_index, 3),
            "volatility_flag": self.flag,
            "dispersion_ratio": round(self.dispersion_ratio, 4),
            "baseline_dispersion": round(self.baseline_dispersion, 4),
            "weeks_observed": self.weeks_observed,
            "sparkline": [round(v, 4) for v in self.sparkline],
        }


def classify(heat_index: float) -> str:
    if heat_index > VOLATILE_THRESHOLD:
        return VOLATILE
    if heat_index >= ELEVATED_THRESHOLD:
        return ELEVATED
    return STABLE


def assess(series: PriceSeries, as_of: int | None = None) -> VolatilityAssessment:
    """Heat index for a part, optionally as of an earlier week.

    `as_of` is an exclusive index into the series, so the backtest can ask what
    the flag would have said at a past point without seeing later weeks.
    """
    points = series.points if as_of is None else series.points[:as_of]
    if not points:
        return VolatilityAssessment(1.0, STABLE, 0.0, 0.0, 0, [])

    dispersions = np.array([p.dispersion_ratio for p in points], dtype=float)

    current = float(np.mean(dispersions[-CURRENT_WINDOW_WEEKS:]))
    baseline_window = dispersions[-BASELINE_WINDOW_WEEKS:]
    baseline = float(np.median(baseline_window))

    if baseline < MIN_BASELINE:
        # No usable baseline: report calm rather than inventing a huge ratio.
        heat = 1.0
    else:
        heat = current / baseline

    # The sparkline is the price the buyer is being asked to reason about, not
    # the dispersion that produced the flag.
    sparkline = [p.p50 for p in points[-SPARKLINE_WEEKS:]]

    return VolatilityAssessment(
        heat_index=heat,
        flag=classify(heat),
        dispersion_ratio=current,
        baseline_dispersion=baseline,
        weeks_observed=len(points),
        sparkline=sparkline,
    )
