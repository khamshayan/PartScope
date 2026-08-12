# Source Scope

RFQ triage for the electronic-component secondary market: paste a messy request
for quote, get back a clean sheet of matched parts, price bands, forecasts and
counterfeit-test recommendations.

> **Demo data — synthetically generated.** Every part and every price in this
> project is fabricated by a seeded generator. See
> [docs/data-sources.md](docs/data-sources.md).

---

## Status

Built in phases. This is what currently works:

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold, Docker datastores, synthetic data generators | **done** |
| 1 | Part matcher + accuracy evaluation | not started |
| 2 | Pricing engine, forecasters, backtest | not started |
| 3 | FastAPI + Express service layer | not started |
| 4 | React dashboard | not started |
| 5 | Email and spreadsheet parsing | not started |
| 6 | AS6171 test-flow routing | not started |
| 7 | Packaging and docs | not started |

## Quickstart

Requires Docker, Python 3.11+ and Node 20+.

```bash
cp .env.example .env      # defaults work as-is; no API keys needed
make setup                # venv + python deps + npm deps
make up                   # postgres + mongo via docker compose
make seed                 # generate the synthetic dataset
make verify               # prove the data has the properties we claim
```

On Windows, where `make` is not installed, the same targets are available as
`./make.ps1 setup`, `./make.ps1 up`, and so on.

## The problem

When a component goes obsolete or lands on allocation, authorized distributors
sell out and buyers move to the secondary market — independent brokers and
stockholders. Three things make that market painful:

- **Identity is messy.** The same physical part arrives as `STM32F103C8T6`,
  `stm32f103c8`, `296-STM32F103C8T6-ND`, or `STM32FI03C8T6` with an I for a 1.
- **Pricing is chaotic.** With no authorized anchor, cost tracks perceived
  scarcity. Identical parts get quoted hundreds of percent apart on the same day.
- **Authenticity is uncertain.** Counterfeit risk means lab testing against
  standards like AS6171, but a full test flow is expensive — so deciding how
  much testing a given part warrants is a judgement call.

Source Scope automates the first pass over all three.

## Architecture

```
browser (React + Vite)
    |
    v
Express API  :3000  ---- MongoDB   parts catalog (specs vary by category)
    |
    v
FastAPI      :8000  ---- PostgreSQL  price history, RFQs, line items
  matching / pricing / parsing / risk
```

Two datastores because the data genuinely differs in shape: `datasheet_specs`
is a different object for an op-amp than for a circular connector, while price
history is 2.8M uniform time-series rows that want window functions and
percentiles. Full rationale in [docs/architecture.md](docs/architecture.md).

## Data

8,000 parts and ~2.8M weekly broker quotes, generated deterministically from one
seed. About a quarter of the catalog is Obsolete or EOL with zero authorized
stock — those are the interesting cases. Roughly 15% of parts carry an injected
shortage event whose quote dispersion widens sharply during the spike, which is
the signal the volatility flag reads.

**None of it is real.** Read [docs/data-sources.md](docs/data-sources.md) before
drawing any conclusion from a number this project prints.

## Licence

Personal portfolio project. Not affiliated with, endorsed by, or reviewed by any
manufacturer or distributor named in the generated data.
