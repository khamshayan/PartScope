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

all_parts = []
for kw in keywords:
    adapter = MouserAdapter(key, keywords=(kw,), limit=30)
    parts = list(adapter.parts())
    print(f"{kw}: {len(parts)} parts")
    all_parts.extend(parts)

written = write_to_mongo(all_parts)
print(f"\nwrote {written} total parts to mongo")