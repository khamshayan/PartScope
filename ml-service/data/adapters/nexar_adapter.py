"""Optional real-data adapter: Nexar (the Octopart API).

THIS IS NOT THE PATH THIS PROJECT RUNS ON, and it is deliberately thin.

It exists to show that the architecture accepts a real feed — the catalog and
price-history sources sit behind an interface, and the synthetic generator is
one implementation of it rather than a hard-coded assumption. Swapping in a live
source is a change of adapter, not a change of pipeline.

It is not the default because a real API would make the project un-runnable for
whoever clones it: Nexar needs an account, a client ID and secret, has rate
limits, and returns different data every day, which would destroy the
reproducibility every number in the README depends on. When NEXAR_API_KEY is
absent — which is the normal case — this logs a clear notice and hands back to
the generator.

If you did want to use it in earnest, the parts that are stubbed are the parts
that matter: OAuth token exchange, the GraphQL queries, pagination, rate-limit
backoff, and mapping Octopart's category taxonomy onto the thirteen categories
this project's `datasheet_specs` shapes are built around. That mapping is the
genuinely hard part and it is not attempted here.

See docs/data-sources.md.
"""

from __future__ import annotations

import logging
from typing import Iterator, Protocol

from app.config import get_settings

log = logging.getLogger("sourcescope.nexar")

NEXAR_GRAPHQL_URL = "https://api.nexar.com/graphql"


class CatalogSource(Protocol):
    """The interface both the generator and this adapter satisfy."""

    def parts(self) -> Iterator[dict]:
        """Yield catalog documents shaped for the Mongo `parts` collection."""
        ...

    def price_history(self, mpns: list[str]) -> Iterator[tuple]:
        """Yield (part_mpn, week_start, broker_id, price, qty, lead_time) rows."""
        ...


class NexarAdapter:
    """Reads a real catalog and offer history from Nexar.

    Every method raises NotImplementedError. That is honest rather than lazy: a
    half-written API client that silently returns partial data would be worse
    than one that refuses, because the pipeline downstream cannot tell the
    difference between "few offers" and "the client gave up on page 2".
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("NexarAdapter requires an API key")
        self.api_key = api_key

    def parts(self) -> Iterator[dict]:
        raise NotImplementedError(
            "Nexar catalog ingestion is not implemented. The unfinished work is "
            "OAuth token exchange, the GraphQL part search, pagination, and "
            "mapping Octopart's taxonomy onto this project's 13 categories."
        )

    def price_history(self, mpns: list[str]) -> Iterator[tuple]:
        raise NotImplementedError(
            "Nexar offer history is not implemented. Note that Nexar returns "
            "CURRENT distributor offers, not the weekly broker-quote time series "
            "this project's forecasters train on -- building that history means "
            "capturing offers on a schedule and accumulating it over months."
        )


def get_catalog_source() -> tuple[object, str]:
    """Return the active data source and a one-line description of why.

    Called by the seed path. The fallback is not an error condition: synthetic
    data is the intended default, and this reports which source is in use so it
    can never be ambiguous which one produced the numbers on screen.
    """
    from data import generate_catalog

    api_key = get_settings().nexar_api_key
    if not api_key:
        log.info(
            "NEXAR_API_KEY not set - using the synthetic generator. "
            "This is the intended default; see docs/data-sources.md."
        )
        return generate_catalog, "synthetic (seeded generator)"

    log.warning(
        "NEXAR_API_KEY is set, but the Nexar adapter is a stub and raises on "
        "use. Falling back to the synthetic generator. See "
        "ml-service/data/adapters/nexar_adapter.py."
    )
    return generate_catalog, "synthetic (Nexar adapter is a stub)"
