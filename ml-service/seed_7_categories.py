import sys
sys.path.insert(0, ".")
sys.path.insert(0, "data")
from app.config import get_settings
from adapters.mouser_adapter import MouserAdapter
from generate_catalog import write_to_mongo

key = get_settings().mouser_api_key
keywords = [
    "thick film chip resistor",
    "mlcc ceramic capacitor",
    "tantalum capacitor",
    "ldo voltage regulator",
    "power mosfet",
    "8-bit microcontroller",
    "crystal oscillator",
]

LIMIT_PER_CATEGORY = 1000

good_parts = []
total_fetched = 0
for kw in keywords:
    adapter = MouserAdapter(key, keywords=(kw,), limit=LIMIT_PER_CATEGORY)
    parts = list(adapter.parts())
    total_fetched += len(parts)
    with_specs = [p for p in parts if p.get("datasheet_specs")]
    dropped = len(parts) - len(with_specs)
    print(f"{kw}: {len(parts)} fetched, {len(with_specs)} kept, {dropped} dropped (empty specs)")
    good_parts.extend(with_specs)

written = write_to_mongo(good_parts)
print(f"\nfetched {total_fetched} total, kept {len(good_parts)} with real specs")
print(f"wrote {written} parts to mongo")
