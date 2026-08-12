# Backtest results

> **The data is synthetic.** These numbers describe how three forecasters behave
> on a seeded generator, not on a market. The methodology is what transfers; the
> percentages do not. See [data-sources.md](data-sources.md).

## Setup

| | |
|---|---|
| Parts | 500, stratified 50/50 across active and obsolete |
| Split | Rolling-origin walk-forward: train `0..t`, predict `t+1`, advance |
| Training window | Weeks 0–77 initially, growing at each origin |
| Test origins | 25 (weeks 78–102), predicting weeks 79–103 |
| Predictions | 37,500 (500 parts × 25 origins × 3 models) |
| Runtime | 2,331s |

Nothing is shuffled. No model sees week `t+1` or later when predicting week
`t+1`. Reproduce with `python scripts/backtest.py`; charts are in
[`ml-service/notebooks/backtest_report.ipynb`](../ml-service/notebooks/backtest_report.ipynb).

## Results

| Segment | Model | n | MAPE | Median APE | RMSE ($) |
|---|---|---:|---:|---:|---:|
| All parts | `naive` | 12,500 | 6.5% | 5.2% | 6.50 |
| All parts | `sarima` | 12,500 | 6.8% | 4.7% | 6.53 |
| All parts | **`gbm`** | 12,500 | **5.3%** | **4.2%** | **5.21** |
| Active / NRND | `naive` | 6,250 | 6.3% | 5.1% | 2.91 |
| Active / NRND | `sarima` | 6,250 | 5.8% | 4.6% | 2.38 |
| Active / NRND | **`gbm`** | 6,250 | **5.0%** | **4.1%** | 2.46 |
| Obsolete / EOL | `naive` | 6,250 | 6.7% | 5.2% | 8.72 |
| Obsolete / EOL | `sarima` | 6,250 | 7.8% | 4.9% | 8.92 |
| Obsolete / EOL | **`gbm`** | 6,250 | **5.5%** | **4.3%** | **6.95** |
| **During a shortage spike** | `naive` | 516 | 13.5% | 9.5% | 4.00 |
| **During a shortage spike** | `sarima` | 516 | **23.8%** | 11.9% | 4.07 |
| **During a shortage spike** | **`gbm`** | 516 | **10.3%** | **7.6%** | 3.96 |
| Normal weeks | `naive` | 11,984 | 6.2% | 5.1% | 6.59 |
| Normal weeks | `sarima` | 11,984 | 6.1% | 4.6% | 6.62 |
| Normal weeks | **`gbm`** | 11,984 | **5.0%** | **4.1%** | **5.26** |

Closest forecast, counted per part-week: `gbm` 39.4%, `sarima` 30.5%,
`naive` 30.1%.

**Read MAPE, not RMSE.** This catalog spans $0.01 resistors to $800 FPGAs, so
RMSE in dollars mostly ranks the models by how they do on expensive parts. It is
reported because it was asked for, not because it is the better summary.

## What the numbers actually say

### The naive baseline is hard to beat, and that is the expected result

A random walk wins 30.1% of part-weeks outright and posts 6.5% MAPE overall,
within a point of SARIMA. On a stable, liquid part next week's price genuinely
is this week's price plus noise, and no amount of modelling improves on that —
there is nothing there to model.

This is the outcome to expect, and a model comparison where the sophisticated
approach sweeps every segment usually means the evaluation leaked. The useful
question is not "which model wins" but "where does anything beat the baseline",
and the answer here is narrow and specific: during shortages, where naive is
13.5% and the booster is 10.3%.

### SARIMA is better most weeks and worse on average

The most interesting row in the table is SARIMA's overall line:

- **Median** APE 4.7% — the *best* of the three
- **Mean** APE 6.8% — the *worst* of the three

Both are correct. SARIMA is more accurate than naive in a typical week and
occasionally catastrophically wrong, and the mean is where those tails land.
The spike segment shows where they come from: 23.8% MAPE, nearly double the
naive baseline it is supposed to improve on.

The mechanism is straightforward. SARIMA fits a linear model to the log price,
so when a shortage sends a price up 4x in three weeks the fitted AR terms read
that as a trend and extrapolate it. Prices then decay back exponentially, and
the model is still climbing. Naive at least tracks the level; SARIMA confidently
overshoots.

**This matters for what gets shipped.** A model whose errors cluster precisely
in the weeks a buyer is under pressure is worse than its average suggests, which
is why SARIMA is not the default in the service even though its median error is
excellent.

### Gradient boosting wins, and it is worth understanding why

It leads every segment on MAPE, and roughly halves the spike-week error relative
to SARIMA. Two reasons, neither of them "it is a better model class":

1. **It predicts a ratio, not a level.** The target is `log(price[t+1] /
   price[t])`, which is comparable across a catalog spanning five orders of
   magnitude. A model predicting levels would spend all its capacity on the
   expensive parts.
2. **It sees dispersion.** Its features include the current 8-week dispersion,
   the 52-week baseline, and weeks-since-last-spike — the same signal the heat
   index reads. It therefore has a way to know a part is *mid-shortage*, and
   spikes decay rather than continue. Neither naive nor SARIMA has any such
   input; both see only the price path.

The honest caveat: the booster is trained across all parts and the generator
built every shortage from one decay process, so there is a single consistent
pattern to learn. Real shortages differ part to part and are correlated across
whole categories. **This gap would narrow on real data**, and quite possibly
invert during a shortage the training window has no precedent for.

### The seasonal term was never selected — 0 out of 500 parts

The generator injects an explicit annual seasonal component, and AIC preferred a
seasonal order (s=52) for **none** of the 500 parts. Selected orders were
overwhelmingly plain and short: `(1,0,1)` for 182 parts, `(1,0,0)` for 99,
`(0,1,1)` for 59.

This is a real result, not a bug, and it is worth stating plainly because the
brief anticipated seasonality mattering:

- The injected seasonal amplitude is 1.5–6%, against ~2% weekly observation
  noise plus a random walk. The signal is small relative to what sits on top
  of it.
- At s=52 with 104 observations there are exactly **two** cycles. A seasonal AR
  term must pay for itself against only two realisations of the pattern, and
  AIC's parameter penalty is not recovered.

The lesson generalises past this dataset: two years of weekly data is not enough
to identify annual seasonality unless the seasonal swing is large relative to
the noise. Fitting it anyway would have added cost and variance for nothing.
Detecting that and dropping the term is the model selection working correctly.

## Deviations from the brief's method

**SARIMA parameters are estimated once per part**, on the initial 78-week
window; later origins update the model's state with each newly observed week via
`append(refit=False)` rather than re-estimating coefficients. The forecast for
week `t+1` still conditions only on weeks `0..t` — no leakage — but SARIMA is
not given the same per-origin refit budget the gradient booster gets, which is
refit from scratch at all 25 origins.

This was a compute decision: re-estimating per origin is 12,500 additional fits
on top of order selection, hours instead of the 39 minutes this run took. It
plausibly costs SARIMA some accuracy, particularly in the spike segment where
stale coefficients hurt most, and the comparison should be read with that in
mind.

**Order search is bounded** by a wall-clock budget checked between candidates,
not a hard per-fit timeout — `SIGALRM` does not exist on Windows.

## What this means for the product

The service defaults to the gradient booster. That is a decision made *by* this
backtest, not before it, and the reasoning is on the record above.

The wider point is that all three models sit between 5% and 7% MAPE in normal
weeks and diverge sharply during shortages. Forecasting a calm market is
close to free; the value is concentrated in the ~4% of part-weeks where the
market is disturbed. That is also precisely where the volatility flag fires —
so the flag, not the forecast, is the thing worth putting in front of a buyer
first. The forecast tells them what next week costs; the flag tells them whether
to believe it.
