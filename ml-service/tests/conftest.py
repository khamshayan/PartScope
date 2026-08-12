"""Shared fixtures.

The matcher tests need the seeded catalog. Rather than mocking it -- which would
test the cascade against a fiction and tell us nothing about real accuracy --
they run against the actual Mongo catalog and skip cleanly when it is absent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ML_SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_SERVICE_ROOT))

TEST_CASES_PATH = ML_SERVICE_ROOT / "data" / "test_cases.json"


@pytest.fixture(scope="session")
def catalog_index():
    from app.matching import load_from_mongo

    try:
        return load_from_mongo()
    except Exception as exc:  # noqa: BLE001 - any connection failure is a skip
        pytest.skip(f"seeded catalog unavailable ({exc}). Run `make up && make seed`.")


@pytest.fixture(scope="session")
def matcher(catalog_index):
    from app.matching import PartMatcher

    return PartMatcher(catalog_index)


@pytest.fixture(scope="session")
def test_cases() -> list[dict]:
    return json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))["cases"]
