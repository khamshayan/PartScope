# Data sources

**The parts are real. Every price is synthetic. No number here is real market
data.**

That statement is not a disclaimer buried at the bottom of a page — it is the
first thing in this document because it is the most important thing to know
about the results. The deployed catalog holds real components with real part
numbers and real specifications. Everything attached to them that looks like
money — price bands, forecasts, market heat, shortage events — is generated.

Real part numbers carrying invented prices look more authoritative than either
half deserves, so it is worth being blunt: **do not use anything here to make a
purchasing decision.**

## Which dataset is which

| Dataset | Rows | Source | Store | Real? |
|---|---|---|---|---|
| Parts catalog, deployed | 6,286 | Mouser Search API, via [`mouser_adapter.py`](../ml-service/data/adapters/mouser_adapter.py) | MongoDB | **Real** |
| Parts catalog, `make seed` | 8,000 | [`generate_catalog.py`](../ml-service/data/generate_catalog.py) | MongoDB | Synthetic |
| Broker price quotes | ~2.2M / ~2.8M | [`generate_price_history.py`](../ml-service/data/generate_price_history.py) | PostgreSQL | Synthetic |

The default local path is the generator: `make seed` produces the 8,000-part
synthetic catalog and its price history, and needs no API key or network access.
The real catalog is a separate manual step
([`seed_7_categories.py`](../ml-service/seed_7_categories.py)) requiring a
`MOUSER_API_KEY`, followed by
[`generate_real_price_history.py`](../ml-service/generate_real_price_history.py)
to generate prices against the real part numbers.

Both generators are deterministic functions of a single integer seed
(`SEED_RANDOM_SEED`, default `20240815`). Two people running `make seed` get
byte-identical databases, which is why the accuracy and backtest numbers quoted
in the README are reproducible rather than anecdotal. **Those numbers are
measured against the synthetic catalog** — the matcher's 60 test cases reference
generated part numbers, so they resolve only in that state.

The real catalog is not reproducible in the same way: a live distributor feed
changes daily, and re-running the seed on another day yields a different set.

## The real catalog

6,286 parts across six categories: 1,821 fixed capacitors, 1,006 voltage
regulators, 963 fixed resistors, 948 crystal oscillators, 935 microcontrollers
and 613 power FETs. Part numbers, manufacturers, packages, lifecycle status,
authorized stock and `datasheet_specs` all come from Mouser's own product data.

Six categories rather than the thirteen the generator covers. The other seven —
zener diodes, logic gates, op-amps, FPGAs, SRAM, flash and circular connectors —
returned descriptions too inconsistent to parse specifications from, and a part
with no specs contributes nothing to the alternates ranking, so they are not
seeded.

Three properties of the feed shape what the rest of the project can do with it:

- **There is no price history.** `price_history()` on the adapter raises. Mouser
  publishes current distributor price breaks — one point in time from one
  authorized seller — not the weekly multi-broker quote series the forecasters
  train on.
- **Two fields are not in the feed.** `date_introduced` is empty, so the risk
  model's "introduced more than 15 years ago" rule never fires on real parts.
  `is_defense_grade` is inferred from military part-number markers (JAN
  prefixes, M39014 / M38510 slash sheets, MIL-DTL, D38999) rather than read from
  a flag — a real signal, but a heuristic.
- **Most specs come from the description, not the attributes.** Mouser's
  `ProductAttributes` are frequently just packaging and pack quantity, while the
  actual values sit in the description text (`"630V 0.033uF C0G 1210 5%"`). The
  adapter parses those by unit rather than by position, and omits any field
  whose unit is absent rather than inferring it.

Real distributor data is also much healthier than generated data: 6,218 of the
6,286 parts are Active, 58 NRND and 10 EOL, and only nine have no authorized
stock. The synthetic catalog deliberately puts about a quarter of its parts in
Obsolete or EOL with zero stock, because those are the cases the product is
about. This is the main reason the generator has not been retired.

## The synthetic catalog

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
had any involvement in this project. No manufacturer supplied data for it
either: the real catalog described above comes from Mouser's public distributor
API, not from any manufacturer directly.

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
- **No distributor or broker pricing, scraped or otherwise.** Mouser's price
  breaks are returned by the API and deliberately not stored: the `price_tier`
  field derived from them feeds only the synthetic price generator, and no
  Mouser price is ever displayed or forecast.
- No customer, RFQ, or transaction data from any company.
- No external API calls in the default pipeline. `make seed` needs no key and no
  network; the Mouser catalog is a separate, explicit step.

## The adapters

Two adapters sit behind the same interface as the generator, and nothing in this
repository ships with credentials.

[`mouser_adapter.py`](../ml-service/data/adapters/mouser_adapter.py) (Mouser
Search API) is **implemented, and is where the deployed catalog comes from**.
`parts()` fetches real records, maps Mouser's taxonomy onto this project's
categories, and builds `datasheet_specs` in the same shapes the generator
produces — from `ProductAttributes` where they exist, and otherwise by parsing
the description text by unit. Parts that map to no category, or whose specs come
back empty, are skipped rather than filed under a guess. Run it against your own
key with:

```
python ml-service/data/adapters/mouser_adapter.py --limit 50
```

It is not exercised by the test suite: the suite must pass offline, and this
adapter cannot.

[`nexar_adapter.py`](../ml-service/data/adapters/nexar_adapter.py) (Nexar /
Octopart) is a **stub**: every method raises, and the docstring says which parts
are unbuilt. It exists to mark the seam.

## What actually transfers

The methodology transfers. The numbers do not.

The normalization rules, the matching cascade, the rolling-origin backtest
design, the dispersion-based volatility measure and the risk-scoring model are
all things you would apply unchanged to a real feed. They are the substance of
the project.

The specific accuracy figures and forecast errors are properties of the
synthetic dataset. Real secondary-market data is messier in ways this generator
does not simulate: quotes cluster and copy each other rather than being drawn
independently, listings are stale or fictitious, the same physical part appears
under several manufacturer names after acquisitions, and shortage events are
correlated across whole categories rather than striking parts independently. A
matcher scoring well here would need re-evaluation before anyone trusted it on
real inputs, and the forecast error on real data would be higher.

Half of that gap has now closed on the catalog side. The matcher faces genuine
manufacturer naming conventions rather than imitations of them — real suffixes,
real packaging codes, real inconsistency between families. What has not happened
is a re-measurement: the 60 test cases still reference generated part numbers,
so the published accuracy figures describe the synthetic catalog and nothing
else. Building a labelled set against the real catalog is the outstanding work,
and until it exists no accuracy claim covers the deployed system.

Nothing has changed on the pricing side. Every forecast number remains a
property of the generator.

See [backtest-results.md](backtest-results.md) for the model comparison and the
commentary on where each model wins.
