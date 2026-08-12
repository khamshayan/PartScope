"""Suggest cross-manufacturer alternates for a matched part.

The buyer's real question when a part is obsolete is "what else will do?", so
for any match we return up to five parts in the same category from a *different*
manufacturer, ranked by how close their datasheet specs are.

Distance is computed over numeric spec fields only, each scaled by the spread
that field actually has within its category. Without that scaling a field
measured in nanocoulombs would drown out one measured in volts purely because
its numbers are bigger.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .catalog import CatalogIndex, CatalogPart

# The one or two fields that decide whether a substitution is even worth
# considering. A 3.3V regulator is not an alternate for a 12V one however well
# the rest of the sheet matches, so these carry extra weight.
HEADLINE_FIELDS = {
    "Microcontrollers": {"flash_kb": 3.0, "ram_kb": 2.0, "max_freq_mhz": 2.0},
    "Operational Amplifiers": {"gbw_mhz": 3.0, "channels": 3.0},
    "Fixed Capacitors": {"capacitance_pf": 4.0, "voltage_rating_v": 3.0},
    "Fixed Resistors": {"resistance_ohm": 4.0, "power_rating_w": 2.0},
    "Logic Gates": {"channels": 3.0},
    "Zener Diodes": {"zener_voltage_v": 4.0, "power_dissipation_mw": 2.0},
    "Power FETs": {"vds_max_v": 3.0, "rds_on_mohm": 3.0, "id_continuous_a": 2.0},
    "Voltage Regulators": {"output_voltage_v": 4.0, "output_current_max_a": 3.0},
    "FPGAs": {"logic_elements": 4.0, "user_io_count": 2.0},
    "SRAM": {"density_kbit": 4.0, "access_time_ns": 2.0},
    "Flash Memory": {"density_mbit": 4.0, "max_clock_mhz": 2.0},
    "MIL-Spec Circular Connectors": {"contact_count": 4.0, "shell_size": 3.0},
    "Crystal Oscillators": {"frequency_mhz": 4.0, "frequency_tolerance_ppm": 2.0},
}

# Fields that describe packaging or environment rather than function. They still
# count, but a different temperature range should not outrank a different
# capacitance.
LOW_WEIGHT_FIELDS = {"temp_range.min_c", "temp_range.max_c", "esr_mohm",
                     "tcr_ppm", "mating_cycles", "erase_cycles",
                     "data_retention_years", "page_size_bytes"}

MAX_ALTERNATES = 5


def flatten_specs(specs: dict, prefix: str = "") -> dict[str, float]:
    """Flatten nested specs to dotted keys, keeping only numeric leaves.

    Booleans are skipped: in Python they are ints, and treating rail_to_rail as
    a number would let it contribute a spurious distance of 1.0.
    """
    flat: dict[str, float] = {}
    for key, value in specs.items():
        name = f"{prefix}{key}"
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            flat[name] = float(value)
        elif isinstance(value, dict):
            flat.update(flatten_specs(value, prefix=f"{name}."))
    return flat


@dataclass
class CategoryStats:
    """Per-field spread within one category, used to scale distances."""

    spread: dict[str, float]

    @classmethod
    def from_parts(cls, parts: list[CatalogPart]) -> "CategoryStats":
        values: dict[str, list[float]] = {}
        for part in parts:
            for field, value in flatten_specs(part.datasheet_specs).items():
                values.setdefault(field, []).append(value)

        spread: dict[str, float] = {}
        for field, series in values.items():
            if len(series) < 2:
                continue
            series.sort()
            # Interquartile range, not min-max: a single exotic part should not
            # compress every ordinary comparison into the noise.
            lo = series[len(series) // 4]
            hi = series[(3 * len(series)) // 4]
            width = hi - lo
            if width <= 0:
                width = abs(series[-1] - series[0]) or abs(series[0]) or 1.0
            spread[field] = float(width)
        return cls(spread=spread)


class AlternateFinder:
    def __init__(self, index: CatalogIndex):
        self.index = index
        # Computed once per category, not per request.
        self.stats: dict[str, CategoryStats] = {
            category: CategoryStats.from_parts(parts)
            for category, parts in index.by_category.items()
        }

    def _weight(self, category: str, field: str) -> float:
        root = field.split(".")[0]
        headline = HEADLINE_FIELDS.get(category, {})
        if root in headline:
            return headline[root]
        if field in LOW_WEIGHT_FIELDS or root in LOW_WEIGHT_FIELDS:
            return 0.3
        return 1.0

    def distance(self, part: CatalogPart, other: CatalogPart) -> float:
        """Weighted mean normalized difference over shared numeric fields.

        Returns +inf when the two share no comparable fields, so incomparable
        parts sort to the bottom rather than looking like a perfect match.
        """
        left = flatten_specs(part.datasheet_specs)
        right = flatten_specs(other.datasheet_specs)
        shared = left.keys() & right.keys()
        if not shared:
            return math.inf

        stats = self.stats.get(part.category)
        total = 0.0
        weight_sum = 0.0
        for field in shared:
            scale = (stats.spread.get(field) if stats else None) or 1.0
            weight = self._weight(part.category, field)
            total += weight * min(abs(left[field] - right[field]) / scale, 4.0)
            weight_sum += weight
        return total / weight_sum if weight_sum else math.inf

    def find(self, part: CatalogPart, limit: int = MAX_ALTERNATES) -> list[dict]:
        candidates = self.index.category_peers(part)
        if not candidates:
            return []

        scored = []
        for other in candidates:
            score = self.distance(part, other)
            if not math.isfinite(score):
                continue
            # An obsolete alternate is a worse answer than an active one at the
            # same spec distance -- you would be swapping one sourcing problem
            # for another -- so push it down rather than filtering it out.
            if other.is_obsolete:
                score += 0.6
            if other.authorized_stock == 0:
                score += 0.2
            scored.append((score, other))

        scored.sort(key=lambda pair: (pair[0], pair[1].mpn))
        return [
            {
                "mpn": other.mpn,
                "manufacturer": other.manufacturer,
                "description": other.description,
                "lifecycle_status": other.lifecycle_status,
                "authorized_stock": other.authorized_stock,
                # Reported as similarity so the UI does not have to explain that
                # smaller is better.
                "similarity": round(1.0 / (1.0 + score), 3),
            }
            for score, other in scored[:limit]
        ]
