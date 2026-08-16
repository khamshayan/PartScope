"""Persist the trained serving forecaster between runs.

Fitting the gradient booster takes over a minute -- sklearn's
GradientBoostingRegressor is single-threaded, and this is ~50k rows across ~30
features. Paying that on every service start makes `make dev` a coffee break,
and paying it inside `make seed` would push the seed past its three-minute
budget.

So it is trained once and cached to disk. The cache is keyed by a signature of
everything that would change the model, so a reseed with a different catalog or
a change to the feature builder invalidates it rather than silently serving a
model fitted to data that no longer exists.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from app.config import DATA_DIR

log = logging.getLogger("partscope.model_cache")

CACHE_DIR = DATA_DIR / "cache"
MODEL_PATH = CACHE_DIR / "serving_gbm.joblib"
SIGNATURE_PATH = CACHE_DIR / "serving_gbm.json"

# Bump when the feature builder changes shape. Without this, a cached model
# trained on the old feature layout would be loaded against new features and
# produce confident nonsense rather than an error.
FEATURE_VERSION = 3


def signature(catalog_size: int, training_parts: int, seed: int,
              categories: list[str]) -> str:
    payload = json.dumps({
        "feature_version": FEATURE_VERSION,
        "catalog_size": catalog_size,
        "training_parts": training_parts,
        "seed": seed,
        "categories": categories,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load(expected: str):
    """Return the cached forecaster, or None when it is missing or stale."""
    if not (MODEL_PATH.exists() and SIGNATURE_PATH.exists()):
        return None
    try:
        stored = json.loads(SIGNATURE_PATH.read_text(encoding="utf-8"))
        if stored.get("signature") != expected:
            log.info("cached model is stale (data or features changed); retraining")
            return None
        import joblib

        return joblib.load(MODEL_PATH)
    except Exception as exc:  # noqa: BLE001 - a bad cache is just a cache miss
        log.warning("could not load cached model (%s); retraining", exc)
        return None


def save(forecaster, expected: str, metadata: dict | None = None) -> None:
    try:
        import joblib

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(forecaster, MODEL_PATH)
        SIGNATURE_PATH.write_text(
            json.dumps({"signature": expected, **(metadata or {})}, indent=2),
            encoding="utf-8",
        )
        log.info("cached trained model at %s", MODEL_PATH)
    except Exception as exc:  # noqa: BLE001 - failing to cache must not fail startup
        log.warning("could not cache trained model: %s", exc)


def clear() -> None:
    for path in (MODEL_PATH, SIGNATURE_PATH):
        path.unlink(missing_ok=True)
