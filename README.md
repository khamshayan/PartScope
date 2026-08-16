# PartScope

RFQ triage for the electronic-component secondary market: paste a messy request
for quote, get back a clean sheet of matched parts, price bands, forecasts and
counterfeit-test recommendations.

> **Demo data — synthetically generated.** Every part and every price in this
> project is fabricated by a seeded generator. See
> [docs/data-sources.md](docs/data-sources.md).

![The PartScope dashboard: a messy RFQ on the left, matched and priced line items on the right](docs/images/dashboard.png)

Fifteen messy lines in, fourteen matched, in about 700ms. The rows worth a
buyer's attention are the ones flagged at the bottom — obsolete and end-of-life
parts whose brokers have stopped agreeing on price, five of which route to a
full AS6171 test flow.

Clicking a row shows the reasoning, including the arithmetic behind the test
recommendation:

![Test-flow breakdown: EOL +30, no authorized stock +20, elevated market +8, defense grade +15, introduced 34 years ago +10, total 83, Full AS6171, 36h of a 48h target](docs/images/test-flow.png)

---

## Quickstart

Three commands from clone to running. **No API keys, no accounts, no manual data
setup.** Requires Docker, Python 3.11+ and Node 20+.

```bash
git clone <this-repo> partscope && cd partscope
make setup     # venv, python deps, npm deps, .env
make demo      # databases up, data seeded, all three services running
```

Then open <http://localhost:5173> and press **Load example**.

On Windows, where `make` is not installed, the same targets are available as
`./make.ps1 setup` and `./make.ps1 demo`.

<details>
<summary>Individual targets</summary>

```bash
make up        # postgres + mongo via docker compose
make seed      # generate the synthetic dataset (~35s)
make verify    # prove the data has the properties this README claims
make dev       # ml-service :8000, api :3000, web :5173
make test      # python + node test suites
make clean     # remove containers and volumes
```

The first service start fits and caches the serving forecaster (~70s); later
starts take about 3 seconds.

</details>


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

PartScope automates the first pass over all three.

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

## Matcher accuracy

Measured on 60 hand-written messy inputs in
[`ml-service/data/test_cases.json`](ml-service/data/test_cases.json) — 50 real
parts across nine kinds of mess, plus 10 deliberately non-existent parts that
must come back `no_match`. Reproduce with `make test`.

| Metric | Result |
|---|---|
| Top-1 accuracy | **96.0%** (48/50) |
| Top-3 accuracy | **100%** (50/50) |
| False-positive rate | **0.0%** (0/10 non-existent parts matched) |

| Case kind | n | Top-1 | Top-3 |
|---|---|---|---|
| exact | 7 | 100% | 100% |
| packaging suffix | 8 | 100% | 100% |
| truncation | 7 | 71% | 100% |
| typo | 7 | 100% | 100% |
| lowercase | 4 | 100% | 100% |
| whitespace | 4 | 100% | 100% |
| distributor prefix | 6 | 100% | 100% |
| O/0 confusion | 4 | 100% | 100% |
| I/1 confusion | 3 | 100% | 100% |
| non-existent (correctly rejected) | 10 | 100% | — |

**Both top-1 misses are truncations, and both are unresolvable.**
`SST25VF032B-75I` is a prefix of both `…-75I/MF` and `…-75I/SN`;
`MCP1827-0180E` is a prefix of three parts. The input does not contain the
information needed to choose, so the matcher returns all of them and the
correct part appears in the top 3. Making those score 100% would mean guessing,
and a confident wrong answer is worse here than an honest list.

Latency: ~13 ms per line to match, ~7 ms including alternates, against an
8,000-part in-memory index that takes 550 ms to build once at startup.

## Forecast accuracy

Rolling-origin walk-forward backtest: 500 parts, 25 origins, 37,500 predictions.
Train on weeks `0..t`, predict `t+1`, advance. Full commentary in
[docs/backtest-results.md](docs/backtest-results.md).

| Segment | `naive` | `sarima` | `gbm` |
|---|---:|---:|---:|
| All parts | 6.5% | 6.8% | **5.3%** |
| Active / NRND | 6.3% | 5.8% | **5.0%** |
| Obsolete / EOL | 6.7% | 7.8% | **5.5%** |
| **During a shortage spike** | 13.5% | 23.8% | **10.3%** |
| Normal weeks | 6.2% | 6.1% | **5.0%** |

MAPE; lower is better. Three results worth stating plainly:

- **The naive baseline is hard to beat.** A random walk wins 30% of part-weeks
  outright. On a stable part next week's price really is this week's price plus
  noise, and there is nothing there to model.
- **SARIMA has the best median error (4.7%) and the worst mean (6.8%).** It is
  better than naive most weeks and occasionally catastrophic — it reads a
  shortage spike as a trend and extrapolates while the price is already
  decaying. That is why it is not the default.
- **A seasonal term was selected for 0 of 500 parts.** Two years of weekly data
  is not enough to identify an annual cycle whose amplitude is smaller than the
  noise sitting on it, and AIC correctly declined to pay for one.

## Test-flow routing

How much counterfeit testing a part warrants — rules, not a model, and
deliberately so. A buyer is being asked to spend real money and up to a day and
a half of lab time on this number, and their supplier will argue about it.
*"The gradient booster said 0.83"* does not survive that conversation.

| Signal | Points |
|---|---:|
| Lifecycle Obsolete or EOL | +30 |
| Zero authorized stock | +20 |
| Market VOLATILE (ELEVATED: +8) | +15 |
| Defense / aerospace grade | +15 |
| Introduced more than 15 years ago | +10 |
| Match confidence below 0.8 | +10 |

**0–25 Standard** (4 h) → external visual inspection, marking permanency.
**26–55 Enhanced** (12 h) → adds XRF, dimensional analysis, solderability.
**56–100 Full AS6171** (36 h) → adds X-ray, decapsulation, electrical parameter
testing.

Every recommendation carries the itemised reasons that produced it, and the
estimate is shown against a 48-hour target with a warning as it closes in — a
full flow uses 75% of the target, leaving little room for a re-test. AS6171
defines the test methods; the thresholds are a judgement call modelled on how
distributors triage.

A line that matched nothing gets **no** recommendation. You cannot route a test
flow for a part you have not identified, and a score built from the one signal
that happens to exist would be worse than saying so.

## Getting an RFQ in

### Getting an RFQ in

Three ways in, one shape out. Sample files are in [sample-rfqs/](sample-rfqs/).

**Paste an email body.** Mail headers, greetings, sign-offs, signature blocks
and quoted reply chains are stripped; numbered and bulleted lists are read.
Quantities are understood as `MPN x 500`, `500 pcs MPN`, `QTY: 500`,
`MPN, 500, target $2.50` and tab-separated columns. A target price is never
mistaken for a quantity.

**Upload a spreadsheet.** Columns are identified by **what is in them**, not by
what the header says — each column is scored on the fraction of its cells that
look like part numbers, counts and money. Header text only breaks a tie between
two columns that scored the same on content. That is what lets
`sample-rfqs/messy-bom.xlsx` work: its table starts at row 6 under a title
block, its part column is headed "Component", its quantity column sits to the
*left* of the parts, and the workbook opens on a longer decoy sheet.

The UI states what it decided — *"Read sheet Rev C, data from row 6, parts from
column B ("Component"), quantities from column A ("Required")"* — because a
parser that silently picked the wrong column looks exactly like one that picked
right, until someone orders against it.

## The dashboard

One page. Paste on the left, results on the right.

- **Confidence** renders as a bar; anything under 0.7 is tagged **Review**.
- **Market heat** is a chip — Stable / Elevated / Volatile — always carrying a
  dot *and* the word, never colour alone, because the amber step is deliberately
  below 3:1 against a white surface.
- **Clicking a row** expands it: a 52-week price sparkline, the normalisation
  rules that fired, lifecycle and stock, and cross-manufacturer alternates
  ranked by spec distance.
- **A line that matches nothing** turns amber and offers its three nearest
  misses as buttons; clicking one resolves that row in place rather than
  re-running and re-persisting the whole RFQ.
- **Lines the parser skipped** (headers, prose, quoted replies) are listed under
  the table. A line that silently vanished between the paste and the results is
  the one failure mode a buyer would never catch.

## Data

8,000 parts and ~2.8M weekly broker quotes, generated deterministically from one
seed. About a quarter of the catalog is Obsolete or EOL with zero authorized
stock — those are the interesting cases. About 18% of parts carry an injected
shortage event whose quote dispersion widens sharply during the spike, which is
the signal the volatility flag reads.

**None of it is real.** Read [docs/data-sources.md](docs/data-sources.md) before
drawing any conclusion from a number this project prints.

[`ml-service/data/adapters/nexar_adapter.py`](ml-service/data/adapters/nexar_adapter.py)
sketches the same interface against the Nexar (Octopart) API and falls back to
the generator when `NEXAR_API_KEY` is absent. **It is a stub whose methods
raise** — it exists to show that the data source is behind a seam, not to
provide a working feed. Its docstring says exactly which parts are unbuilt, the
hardest being that Nexar returns current distributor offers rather than the
weekly broker-quote history the forecasters train on.

## What I'd do differently with real data

The methodology transfers. Several of the results would not, and it is worth
being specific about which.

**The forecast comparison would tighten, and might invert.** The gradient
booster beats the naive baseline here partly because every shortage in this
dataset was generated from one exponential-decay process, so there is a single
consistent pattern to learn. Real shortages differ part to part and correlate
across whole categories — an automotive MCU allocation moves a hundred part
numbers at once. I would expect the gap to narrow, and during a shortage with no
precedent in the training window I would expect the booster to do worse than
naive, not better. The first thing I would build is a per-segment monitor that
compares each model against the baseline continuously, so that inversion is
visible rather than assumed away.

**Cross-sectional features would matter more than per-part history.** With real
data the strongest signal for "this part is about to spike" is probably not its
own price path at all — it is that three related parts from the same fab process
already spiked. That means a panel model with category and process-node
features, which is a different shape of problem from the per-part series here.

**The matcher would need a real evaluation set, not 60 hand-written cases.**
96% top-1 on cases I wrote myself is a smoke test, not a measurement — I know
what mess I thought to include. The honest version samples real RFQ line items,
has a human label them, and reports accuracy with a confidence interval. I would
also expect whole categories of mess I did not imagine: OCR output from scanned
faxes, customer-internal part numbers with no relationship to the MPN, and
Chinese-market equivalents.

**Lead time deserves to be a first-class output.** The generator carries
`lead_time_days` and this project barely uses it. In a real shortage the
question is rarely "what does it cost" — it is "can I get it before the line
stops". Price is the easier thing to model, which is not the same as the more
useful one.

**The volatility flag needs calibration against outcomes.** The heat index is
constructed to be sensible, and on synthetic data it detects the spikes that
were injected — which is close to circular. The real test is whether a
VOLATILE flag predicts something a buyer cares about: a failed delivery, a
counterfeit finding, a price that keeps climbing. That needs outcome data this
project does not have, and until it exists the thresholds (1.3 and 2.0) are
reasonable guesses rather than tuned values.

**Two things I would keep unchanged.** Rules-based test-flow routing, because
its value is that a human can audit and dispute it — and that argument gets
stronger with real money at stake, not weaker. And the walk-forward evaluation
discipline, because a random split would have made every number in this README
look better and mean nothing.

## Project status

Built in phases, each with tests.

| Phase | Scope |
|---|---|
| 0 | Scaffold, Docker datastores, synthetic data generators |
| 1 | Part matcher + accuracy evaluation |
| 2 | Pricing engine, forecasters, backtest |
| 3 | FastAPI + Express service layer |
| 4 | React dashboard |
| 5 | Email and spreadsheet parsing |
| 6 | AS6171 test-flow routing |
| 7 | Packaging and docs |

185 tests: 164 Python (`pytest`), 21 Node (`vitest`), plus a TypeScript
typecheck on the web app. `make test` runs the suites.

## Licence

Personal portfolio project. Not affiliated with, endorsed by, or reviewed by any
manufacturer or distributor named in the generated data.

## Development notes

Built with AI assistance. 
