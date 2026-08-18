import sys
sys.path.insert(0, ".")
sys.path.insert(0, "data")
from app.config import get_settings, mongo_client
from generate_price_history import write_to_postgres

settings = get_settings()
client = mongo_client()
parts = list(client[settings.mongo_db]["parts"].find(
    {},
    {"mpn": 1, "price_tier": 1, "lifecycle_status": 1, "authorized_stock": 1, "_id": 0}
))
client.close()

print(f"generating price history for {len(parts)} parts")
rows = write_to_postgres(parts, weeks=settings.history_weeks, seed=settings.seed)
print(f"wrote {rows:,} price rows for {len(parts)} real parts")
