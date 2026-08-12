"""Rolling-origin walk-forward backtest of the three forecasters.

THE SPLIT

For each part, train on weeks 0..t and predict week t+1, then advance t. No
shuffling; no observation from week t+1 or later reaches any model that predicts
week t+1. That is the only evaluation design that means anything for a time
series -- a random train/test split would let a model learn from next month to
predict last month and report a spectacular, meaningless score.

Two places where that discipline actually bites, both handled here:

  * The gradient booster is a global model trained across all parts at once, so
    it is refit at every origin on only those (part, week) rows whose TARGET
    week is <= the origin. One condition, and it is what keeps the pooled
    training set honest.

  * The heat-index segment label is computed from the test week, because it is
    a label for reporting, not a feature. It never reaches a model.

WHERE THIS DEVIATES, AND WHY

SARIMA parameters are estimated once per part on the initial training window;
subsequent origins update the model's state with each newly observed week via
`append(refit=False)` rather than re-estimating parameters from scratch. The
forecast for week t+1 still conditions only on weeks 0..t, so there is no
leakage -- but the coefficients are those fitted on the first window.

This is a compute trade, and it is a large one: re-estimating parameters at
every origin means 500 parts x 25 origins = 12,500 fits on top of order
selection, which is hours rather than minutes. It is a standard variant of
walk-forward evaluation, and it is stated here because a reader comparing the
SARIMA column against the others deserves to know the model was not given the
same refit budget a production system might grant it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .forecasters import (
    GradientBoostForecaster,
    NaiveForecaster,
    OrderCache,
    SarimaForecaster,
)
from .repository import PriceSeries, fetch_series, stratified_sample
from .volatility import VOLATILE_THRESHOLD, assess

MODELS = ("naive", "sarima", "gbm")


@dataclass
class BacktestConfig:
    n_parts: int = 500
    initial_train_weeks: int = 78   # 1.5 years to train on
    n_origins: int = 25             # the final ~6 months are the test period
    seed: int = 20240815
    try_seasonal: bool = True
    sarima_search_budget: float = 8.0


@dataclass
class Prediction:
    mpn: str
    week_index: int
    model: str
    actual: float
    predicted: float
    lifecycle: str
    category: str
    during_spike: bool

    @property
    def abs_pct_error(self) -> float:
        return abs(self.actual - self.predicted) / self.actual if self.actual > 0 else 0.0

    @property
    def sq_error(self) -> float:
        return (self.actual - self.predicted) ** 2


@dataclass
class BacktestReport:
    predictions: list[Prediction]
    config: BacktestConfig
    parts_evaluated: int
    elapsed_seconds: float
    sarima_orders: dict = field(default_factory=dict)

    def segment(self, model: str, predicate=None) -> dict:
        rows = [p for p in self.predictions if p.model == model]
        if predicate:
            rows = [p for p in rows if predicate(p)]
        if not rows:
            return {"n": 0, "mape": float("nan"), "rmse": float("nan"),
                    "median_ape": float("nan")}
        apes = np.array([p.abs_pct_error for p in rows])
        return {
            "n": len(rows),
            "mape": float(np.mean(apes)),
            "median_ape": float(np.median(apes)),
            "rmse": float(np.sqrt(np.mean([p.sq_error for p in rows]))),
        }

    def segments(self) -> dict[str, dict[str, dict]]:
        """Every model against every segment the brief asks to break out."""
        definitions = {
            "overall": None,
            "active": lambda p: p.lifecycle not in ("Obsolete", "EOL"),
            "obsolete": lambda p: p.lifecycle in ("Obsolete", "EOL"),
            "during_spike": lambda p: p.during_spike,
            "normal": lambda p: not p.during_spike,
        }
        return {
            name: {model: self.segment(model, pred) for model in MODELS}
            for name, pred in definitions.items()
        }

    def wins(self) -> dict[str, int]:
        """How often each model is closest, counted per (part, week)."""
        by_point: dict[tuple[str, int], list[Prediction]] = {}
        for p in self.predictions:
            by_point.setdefault((p.mpn, p.week_index), []).append(p)
        tally = {m: 0 for m in MODELS}
        for rows in by_point.values():
            if len(rows) < len(MODELS):
                continue
            tally[min(rows, key=lambda r: r.abs_pct_error).model] += 1
        return tally


def _load_metadata(mpns: list[str]) -> dict[str, dict]:
    from app.config import get_settings, mongo_client

    client = mongo_client()
    try:
        docs = client[get_settings().mongo_db]["parts"].find(
            {"mpn": {"$in": mpns}},
            {"_id": 0, "mpn": 1, "lifecycle_status": 1, "category": 1},
        )
        return {d["mpn"]: d for d in docs}
    finally:
        client.close()


def _sarima_path(series: PriceSeries, origins: list[int],
                 forecaster: SarimaForecaster) -> dict[int, float]:
    """SARIMA predictions across every origin for one part.

    Fits once on the initial window, then walks forward by appending each newly
    observed week without re-estimating parameters. See the module docstring.
    """
    values = np.log(np.clip(series.medians, 1e-6, None))
    order, seasonal = forecaster.select_order(series.head(origins[0] + 1))

    predictions: dict[int, float] = {}
    try:
        result = forecaster._fit(values[: origins[0] + 1], order, seasonal)
    except Exception:  # noqa: BLE001
        return {t: series.medians[t] for t in origins}

    for i, t in enumerate(origins):
        if i > 0:
            try:
                result = result.append(values[t: t + 1], refit=False)
            except Exception:  # noqa: BLE001
                predictions[t] = series.medians[t]
                continue
        try:
            predicted = float(np.exp(result.forecast(steps=1)[0]))
        except Exception:  # noqa: BLE001
            predicted = series.medians[t]
        if not np.isfinite(predicted) or predicted <= 0:
            predicted = series.medians[t]
        predictions[t] = predicted
    return predictions


def run_backtest(config: BacktestConfig | None = None, progress=print) -> BacktestReport:
    config = config or BacktestConfig()
    started = time.perf_counter()

    progress(f"sampling {config.n_parts} parts (stratified)...")
    mpns = stratified_sample(config.n_parts, seed=config.seed)
    metadata = _load_metadata(mpns)

    progress("loading price history...")
    all_series = fetch_series(mpns)

    needed = config.initial_train_weeks + config.n_origins + 1
    dataset = [
        (series, metadata.get(mpn, {}))
        for mpn, series in all_series.items()
        if len(series) >= needed
    ]
    if not dataset:
        raise RuntimeError("no parts have enough history for this configuration")

    origins = [config.initial_train_weeks + i for i in range(config.n_origins)]
    categories = sorted({meta.get("category", "") for _, meta in dataset})
    progress(f"{len(dataset)} parts x {len(origins)} origins "
             f"(weeks {origins[0]}..{origins[-1]}, predicting t+1)")

    predictions: list[Prediction] = []

    # Segment labels, computed once per (part, origin) and shared by all models.
    spike_labels: dict[tuple[str, int], bool] = {}
    for series, _ in dataset:
        for t in origins:
            heat = assess(series, as_of=t + 2).heat_index
            spike_labels[(series.mpn, t)] = heat > VOLATILE_THRESHOLD

    def record(mpn, t, model, actual, predicted, meta):
        predictions.append(Prediction(
            mpn=mpn, week_index=t, model=model,
            actual=float(actual), predicted=float(predicted),
            lifecycle=meta.get("lifecycle_status", "Active"),
            category=meta.get("category", ""),
            during_spike=spike_labels[(mpn, t)],
        ))

    # -- naive --------------------------------------------------------------
    progress("naive...")
    naive = NaiveForecaster()
    for series, meta in dataset:
        medians = series.medians
        for t in origins:
            record(series.mpn, t, "naive",
                   medians[t + 1],
                   naive.forecast(series.head(t + 1)).predicted_price, meta)

    # -- gradient boosting --------------------------------------------------
    # Refit at every origin so the training set never contains a target week
    # later than the origin.
    progress("gradient boosting (refit per origin)...")
    for t in origins:
        gbm = GradientBoostForecaster(categories=categories)
        gbm.fit(dataset, up_to=t)
        for series, meta in dataset:
            record(series.mpn, t, "gbm",
                   series.medians[t + 1],
                   gbm.forecast(series.head(t + 1), meta).predicted_price, meta)
        progress(f"    origin {t} done")

    # -- sarima -------------------------------------------------------------
    progress("sarima (order search per part, then walk forward)...")
    cache = OrderCache()
    sarima = SarimaForecaster(cache=cache, try_seasonal=config.try_seasonal,
                              search_budget_seconds=config.sarima_search_budget)
    for i, (series, meta) in enumerate(dataset, start=1):
        path = _sarima_path(series, origins, sarima)
        for t in origins:
            record(series.mpn, t, "sarima", series.medians[t + 1], path[t], meta)
        if i % 25 == 0:
            progress(f"    {i}/{len(dataset)} parts "
                     f"({time.perf_counter() - started:.0f}s elapsed)")

    orders = {mpn: {"order": list(o), "seasonal_order": list(s)}
              for mpn, (o, s) in cache._orders.items()}

    return BacktestReport(
        predictions=predictions,
        config=config,
        parts_evaluated=len(dataset),
        elapsed_seconds=time.perf_counter() - started,
        sarima_orders=orders,
    )


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def format_table(report: BacktestReport) -> str:
    segments = report.segments()
    lines = []
    header = f"{'segment':<16}{'model':<10}{'n':>7}{'MAPE':>9}{'median APE':>13}{'RMSE ($)':>12}"
    lines.append(header)
    lines.append("-" * len(header))
    for name, per_model in segments.items():
        for model in MODELS:
            stats = per_model[model]
            lines.append(
                f"{name:<16}{model:<10}{stats['n']:>7,}"
                f"{stats['mape']:>8.1%}{stats['median_ape']:>13.1%}"
                f"{stats['rmse']:>12.4f}"
            )
        lines.append("")
    tally = report.wins()
    total = sum(tally.values()) or 1
    lines.append("closest forecast, counted per part-week:")
    for model, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {model:<10}{count:>7,}  ({count / total:.1%})")
    return "\n".join(lines)
