# Backtest results

**Not yet produced.** This document is written by Phase 2, which builds the
three forecasters and evaluates them with a rolling-origin walk-forward split.

It will contain:

- MAPE and RMSE per model (Naive / SARIMA / GradientBoosting), broken out by
  segment: active vs obsolete, during-spike vs normal.
- Actual-vs-predicted charts for a handful of example parts.
- Honest commentary on where each model wins. The naive baseline is expected to
  be hard to beat on stable, high-volume parts — if that is what the numbers
  say, that is what this document will say, because a model comparison where
  one approach sweeps every segment usually means the evaluation leaked.

The data these results are computed on is synthetic; see
[data-sources.md](data-sources.md) for what that does and does not imply.
