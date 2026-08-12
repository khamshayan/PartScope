"""In-memory index over the parts catalog.

8,000 parts is small enough to hold entirely in memory, which lets the matcher
do exact, prefix and fuzzy lookups without a database round trip per candidate.
The index is built once at service startup and reused; rebuilding it on every
request would dominate the response time.

If the catalog ever grew past a few hundred thousand parts this would need to
become a real search index, and the shape of the code -- one class that owns
every lookup path -- is what would make that swap contained.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .normalize import rule_normalize_separators, rule_split_grade_suffix

# Fields carried into match results. Kept explicit rather than passing whole
# Mongo documents around, so the API contract does not drift with the schema.
PART_FIELDS = (
    "mpn", "manufacturer", "category", "description", "package",
    "lifecycle_status", "is_defense_grade", "authorized_stock",
    "datasheet_specs", "date_introduced",
)


@dataclass(frozen=True)
class CatalogPart:
    mpn: str
    manufacturer: str
    category: str
    description: str
    package: str
    lifecycle_status: str
    is_defense_grade: bool
    authorized_stock: int
    datasheet_specs: dict
    date_introduced: str

    # Precomputed at index time, not per request.
    key: str = ""
    base_key: str = ""

    @property
    def is_obsolete(self) -> bool:
        return self.lifecycle_status in ("Obsolete", "EOL")

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in PART_FIELDS}


def _to_part(doc: dict) -> CatalogPart:
    mpn = doc["mpn"]
    key = rule_normalize_separators(mpn.upper())
    base_key, _ = rule_split_grade_suffix(key)
    return CatalogPart(
        mpn=mpn,
        manufacturer=doc.get("manufacturer", ""),
        category=doc.get("category", ""),
        description=doc.get("description", ""),
        package=doc.get("package", ""),
        lifecycle_status=doc.get("lifecycle_status", "Active"),
        is_defense_grade=bool(doc.get("is_defense_grade", False)),
        authorized_stock=int(doc.get("authorized_stock", 0)),
        datasheet_specs=doc.get("datasheet_specs", {}) or {},
        date_introduced=doc.get("date_introduced", ""),
        key=key,
        base_key=base_key,
    )


class CatalogIndex:
    """Every lookup path the matcher needs, over one immutable part list."""

    def __init__(self, parts: Iterable[dict]):
        self.parts: list[CatalogPart] = [_to_part(d) for d in parts]

        # Exact lookup. A list per key, not a single part: different
        # manufacturers genuinely ship the same normalized number (74HC00 is
        # made by four of them), and collapsing that would silently hide
        # alternatives from the buyer.
        self.by_key: dict[str, list[CatalogPart]] = defaultdict(list)
        self.by_base_key: dict[str, list[CatalogPart]] = defaultdict(list)
        self.by_category: dict[str, list[CatalogPart]] = defaultdict(list)

        for part in self.parts:
            self.by_key[part.key].append(part)
            if part.base_key != part.key:
                self.by_base_key[part.base_key].append(part)
            self.by_category[part.category].append(part)

        # Sorted keys let prefix lookups run as a binary search over a
        # contiguous range instead of scanning the catalog.
        self.sorted_keys: list[str] = sorted(self.by_key)

        # Parallel arrays for rapidfuzz, which wants a flat sequence of choices.
        self.fuzz_keys: list[str] = self.sorted_keys

    def __len__(self) -> int:
        return len(self.parts)

    # -- lookup paths ------------------------------------------------------

    def exact(self, key: str) -> list[CatalogPart]:
        return list(self.by_key.get(key, ()))

    def by_base(self, base_key: str) -> list[CatalogPart]:
        """Parts whose own grade-stripped key equals this base."""
        return list(self.by_base_key.get(base_key, ()))

    def prefix(self, prefix: str, limit: int = 25) -> list[CatalogPart]:
        """Every part whose key starts with `prefix`.

        This is the truncation path: someone typed STM32F103C8 and meant one of
        the STM32F103C8xx parts. Returning all of them is the honest answer --
        the input genuinely does not distinguish between them.
        """
        if not prefix:
            return []
        start = bisect.bisect_left(self.sorted_keys, prefix)
        found: list[CatalogPart] = []
        for key in self.sorted_keys[start:]:
            if not key.startswith(prefix):
                break
            found.extend(self.by_key[key])
            if len(found) >= limit:
                break
        return found[:limit]

    def category_peers(self, part: CatalogPart) -> list[CatalogPart]:
        """Same category, different manufacturer -- the alternates candidates."""
        return [p for p in self.by_category.get(part.category, ())
                if p.manufacturer != part.manufacturer and p.mpn != part.mpn]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_from_mongo() -> CatalogIndex:
    from app.config import get_settings, mongo_client

    settings = get_settings()
    client = mongo_client()
    try:
        projection = {field: 1 for field in PART_FIELDS}
        projection["_id"] = 0
        docs = list(client[settings.mongo_db]["parts"].find({}, projection))
    finally:
        client.close()

    if not docs:
        raise RuntimeError(
            "parts catalog is empty -- run `make seed` before starting the service"
        )
    return CatalogIndex(docs)
