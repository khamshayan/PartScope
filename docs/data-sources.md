# Data sources

**Every number in this project is synthetic. None of it is real market data.**

That statement is not a disclaimer buried at the bottom of a page — it is the
first thing in this document because it is the most important thing to know
about the results.

## What is generated, and how

| Dataset | Rows | Generator | Store |
|---|---|---|---|
| Parts catalog | 8,000 | [`ml-service/data/generate_catalog.py`](../ml-service/data/generate_catalog.py) | MongoDB |
| Broker price quotes | ~2.8M | [`ml-service/data/generate_price_history.py`](../ml-service/data/generate_price_history.py) | PostgreSQL |

Both are deterministic functions of a single integer seed (`SEED_RANDOM_SEED`,
default `20240815`). Two people running `make seed` get byte-identical
databases, which is why the accuracy and backtest numbers quoted in the README
are reproducible rather than anecdotal.

### The catalog

Part numbers are generated from per-manufacturer templates that imitate real
naming conventions. `STM32F103C8T6` decomposes into family, pin count, flash
size and package in the way a real STM32 part number does; `CRCW060310K0FKEA`
encodes case size, resistance, and tolerance the way a real Vishay thick-film
resistor does. The specs are generated first and the part number is built from
them, so a part's number, its `datasheet_specs`, its package and its description
all agree with each other.

**The parts themselves do not exist.** The conventions are real; the specific
components are invented. If a generated part number happens to collide with a
real one, the attached specifications, lifecycle status and pricing are still
fabricated and bear no relation to the real component. Do not use anything here
to make a purchasing decision.

Manufacturer names are real companies, used because a synthetic catalog with
invented manufacturer names would not exercise the matching problem realistically
— part numbering conventions are manufacturer-specific, and that is precisely
what the matcher has to cope with. No manufacturer has endorsed, reviewed, or
had any involvement in this project, and no manufacturer's data was used.

### The pricing

Weekly quotes are constructed from a log-normal base price, a slow trend, a
mild annual seasonal term, a random walk, and injected shortage events (a 2×–5×
spike decaying exponentially over 8–20 weeks, applied more often and more
severely to obsolete parts). Several independent broker quotes per part per week
are drawn around the weekly median, with the spread widening sharply during
shortage events.

The shape of that model is a deliberate imitation of documented secondary-market
behaviour during shortages. The *parameters* are chosen to make the dataset
interesting, not fitted to any observed market.

## What was not used

- No commercial or proprietary dataset.
- No scraped distributor or broker pricing.
- No customer, RFQ, or transaction data from any company.
- No external API calls at any point in the pipeline. The project runs offline.

## The optional real-data path

`ml-service/data/adapters/nexar_adapter.py` implements the same interface as the
generator against the Nexar (Octopart) API. It exists to demonstrate that the
architecture accepts a real feed — it is not the default path, it is not
exercised by the test suite, and with no `NEXAR_API_KEY` present the pipeline
logs a notice and uses the generator. Nothing in this repository ships with
credentials.

## What actually transfers

The methodology transfers. The numbers do not.

The normalization rules, the matching cascade, the rolling-origin backtest
design, the dispersion-based volatility measure and the risk-scoring model are
all things you would apply unchanged to a real feed. They are the substance of
the project.

The specific accuracy figures and forecast errors are properties of this
synthetic dataset. Real secondary-market data is messier in ways this generator
does not simulate: quotes cluster and copy each other rather than being drawn
independently, listings are stale or fictitious, the same physical part appears
under several manufacturer names after acquisitions, and shortage events are
correlated across whole categories rather than striking parts independently. A
matcher scoring well here would need re-evaluation before anyone trusted it on
real inputs, and the forecast error on real data would be higher.

See [backtest-results.md](backtest-results.md) for the model comparison and the
commentary on where each model wins.
