"""Three next-week price forecasters behind one interface.

  NaiveForecaster           last observed weekly median. The baseline to beat.
  SarimaForecaster          SARIMAX on the weekly median series, order by AIC.
  GradientBoostForecaster   sklearn GBR on lag and calendar features, trained
                            across all parts at once.

The naive baseline is not a formality. On a stable, liquid part a random walk is
very close to the truth, and any model that cannot beat "last week's price"
there is not earning its complexity. Reporting where each model wins is the
point of the backtest, not picking a single champion.

A note on the GBM being global: its feature set includes lifecycle status and
category one-hots, which only carry information across parts. Fitting it
per-part would leave those columns constant and useless. So it learns one
cross-sectional mapping from "what has this part's price been doing, and what
kind of part is it" to next week -- which is also why it can say something about
a part whose own history is short.
"""

from __future__ import annotations

import time
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from .repository import PriceSeries

# statsmodels is voluble about non-convergence and non-invertibility during a
# grid search where failures are expected and handled.
warnings.filterwarnings("ignore", module="statsmodels")


@dataclass
class ForecastResult:
    model: str
    predicted_price: float
    # Populated when the model has something to report about how it got there.
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "forecast_model": self.model,
            "forecast_price": round(self.predicted_price, 4),
            "forecast_detail": self.detail,
        }


class Forecaster(ABC):
    """One-step-ahead forecaster over a weekly median series."""

    name: str = "base"

    @abstractmethod
    def forecast(self, series: PriceSeries, context: dict | None = None) -> ForecastResult:
        ...


# ---------------------------------------------------------------------------
# Naive
# ---------------------------------------------------------------------------

class NaiveForecaster(Forecaster):
    """Next week equals this week.

    For a price series that is close to a random walk this is the optimal
    one-step forecast, which is exactly why it is the bar.
    """

    name = "naive"

    def forecast(self, series: PriceSeries, context: dict | None = None) -> ForecastResult:
        return ForecastResult(self.name, series.last_median)


# ---------------------------------------------------------------------------
# SARIMA
# ---------------------------------------------------------------------------

# Non-seasonal grid. 27 candidates, each a fast fit on ~100 points.
PDQ_GRID = [(p, d, q) for p in (0, 1, 2) for d in (0, 1) for q in (0, 1, 2)]

# Seasonal candidates. s=52 on 104 weekly observations is two cycles, which is
# the bare minimum for the term to be identifiable at all -- see
# docs/backtest-results.md for what that costs in practice. Kept to a short list
# because each seasonal fit is orders of magnitude slower than a plain ARIMA.
SEASONAL_GRID = [(0, 0, 0, 0), (1, 0, 0, 52), (0, 0, 1, 52)]

MIN_WEEKS_FOR_SEASONAL = 104


class OrderCache:
    """Selected (order, seasonal_order) per part.

    Order selection is the expensive half of SARIMA and its answer barely moves
    week to week, so it is done once and reused. Without this a single forecast
    request would run a 27-point grid search and blow the 400ms budget by two
    orders of magnitude.
    """

    def __init__(self) -> None:
        self._orders: dict[str, tuple] = {}

    def get(self, mpn: str):
        return self._orders.get(mpn)

    def set(self, mpn: str, order, seasonal_order) -> None:
        self._orders[mpn] = (order, seasonal_order)

    def clear(self) -> None:
        self._orders.clear()

    def __len__(self) -> int:
        return len(self._orders)


class SarimaForecaster(Forecaster):
    name = "sarima"

    def __init__(self, cache: OrderCache | None = None,
                 search_budget_seconds: float = 8.0,
                 try_seasonal: bool = True):
        self.cache = cache if cache is not None else OrderCache()
        # Wall-clock budget for the grid search, checked between candidates.
        # A per-fit timeout would need SIGALRM, which does not exist on Windows,
        # so the granularity is one candidate fit.
        self.search_budget_seconds = search_budget_seconds
        self.try_seasonal = try_seasonal

    def _fit(self, values: np.ndarray, order, seasonal_order):
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        # An undifferenced model needs an intercept. Without one, SARIMAX has no
        # mean term at all, so an order of (0,0,0) forecasts exactly 0 -- which
        # in log space is a price of $1.00 regardless of what the part costs.
        # Differenced models must not carry a constant, or the forecast picks up
        # a spurious drift.
        trend = "c" if order[1] == 0 and seasonal_order[1] == 0 else "n"

        model = SARIMAX(
            values,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
            trend=trend,
        )
        # Non-convergence is an expected outcome for most candidates in a grid
        # search -- they are scored by AIC and discarded. Warning about each one
        # would bury every other message the service prints.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return model.fit(disp=False, maxiter=50)

    def select_order(self, series: PriceSeries) -> tuple:
        """Two-stage grid search minimising AIC, within a wall-clock budget.

        Stage 1 searches the 27 non-seasonal orders; each is a fast fit on ~100
        points. Stage 2 tries seasonal terms against the winner only.

        The staging is what makes this affordable. A seasonal term at s=52 puts
        52 states into the filter, so one such fit costs orders of magnitude
        more than a plain ARIMA -- crossing the full grid with both seasonal
        candidates was 54 expensive fits and took 14 seconds per part.
        """
        cached = self.cache.get(series.mpn)
        if cached is not None:
            return cached

        # Model log price: prices are positive and move multiplicatively, and a
        # spike that is +300% in levels is a far more tractable step in logs.
        values = np.log(np.clip(series.medians, 1e-6, None))
        started = time.perf_counter()

        best_aic, best_order = np.inf, (1, 1, 1)
        for order in PDQ_GRID:
            if time.perf_counter() - started > self.search_budget_seconds:
                break
            try:
                aic = float(self._fit(values, order, (0, 0, 0, 0)).aic)
            except Exception:  # noqa: BLE001 - a failed candidate is just skipped
                continue
            if np.isfinite(aic) and aic < best_aic:
                best_aic, best_order = aic, order

        best_seasonal = (0, 0, 0, 0)
        if self.try_seasonal and len(series) >= MIN_WEEKS_FOR_SEASONAL:
            for seasonal in SEASONAL_GRID[1:]:
                if time.perf_counter() - started > self.search_budget_seconds:
                    break
                try:
                    aic = float(self._fit(values, best_order, seasonal).aic)
                except Exception:  # noqa: BLE001
                    continue
                if np.isfinite(aic) and aic < best_aic:
                    best_aic, best_seasonal = aic, seasonal

        self.cache.set(series.mpn, best_order, best_seasonal)
        return best_order, best_seasonal

    def forecast(self, series: PriceSeries, context: dict | None = None) -> ForecastResult:
        if len(series) < 8:
            return NaiveForecaster().forecast(series)

        order, seasonal = self.select_order(series)
        values = np.log(np.clip(series.medians, 1e-6, None))
        try:
            fitted = self._fit(values, order, seasonal)
            predicted = float(np.exp(fitted.forecast(steps=1)[0]))
        except Exception:  # noqa: BLE001
            # A model that will not converge is not a reason to fail a request.
            return ForecastResult(self.name, series.last_median,
                                  {"fallback": "naive", "order": list(order)})

        if not np.isfinite(predicted) or predicted <= 0:
            return ForecastResult(self.name, series.last_median,
                                  {"fallback": "naive", "order": list(order)})

        return ForecastResult(
            self.name,
            predicted,
            {"order": list(order), "seasonal_order": list(seasonal)},
        )


# ---------------------------------------------------------------------------
# Gradient boosting
# ---------------------------------------------------------------------------

LAGS = (1, 2, 4, 8, 12)
ROLLING_WINDOWS = (4, 12)

LIFECYCLE_LEVELS = ("Active", "NRND", "Obsolete", "EOL")


def build_features(medians: np.ndarray, dispersions: np.ndarray, t: int,
                   lifecycle: str, category_index: int,
                   n_categories: int) -> np.ndarray | None:
    """Feature row describing the state at week `t`, using only weeks <= t.

    Returns None when there is not enough history behind `t` to fill the lags,
    which is what keeps the training set free of half-imputed rows.
    """
    if t < max(LAGS):
        return None

    current = medians[t]
    if current <= 0:
        return None

    row: list[float] = []

    # Lags as ratios to the current price, not absolute levels: the model has to
    # generalise across parts spanning $0.01 to $800, and a ratio is comparable
    # across all of them where a dollar amount is not.
    for lag in LAGS:
        row.append(medians[t - lag] / current)

    for window in ROLLING_WINDOWS:
        chunk = medians[t - window + 1: t + 1]
        row.append(float(np.mean(chunk)) / current)
        row.append(float(np.std(chunk)) / current)

    # Where the part sits relative to its own recent range.
    year = medians[max(0, t - 51): t + 1]
    row.append(current / float(np.max(year)))
    row.append(current / float(np.min(year)))

    # Dispersion now and versus baseline -- the same signal the heat index uses.
    row.append(float(np.mean(dispersions[max(0, t - 7): t + 1])))
    baseline = float(np.median(dispersions[max(0, t - 51): t + 1]))
    row.append(baseline)

    # Weeks since the last visible dispersion spike, capped. Encodes "we are
    # partway through a shortage", which is when naive does worst.
    window = dispersions[max(0, t - 51): t + 1]
    threshold = baseline * 2.0 if baseline > 0 else np.inf
    spikes = np.nonzero(window > threshold)[0]
    row.append(float(len(window) - 1 - spikes[-1]) if len(spikes) else 52.0)

    # Log price level: cheap parts and expensive parts behave differently.
    row.append(float(np.log(current)))

    for level in LIFECYCLE_LEVELS:
        row.append(1.0 if lifecycle == level else 0.0)

    category_onehot = [0.0] * n_categories
    if 0 <= category_index < n_categories:
        category_onehot[category_index] = 1.0
    row.extend(category_onehot)

    return np.array(row, dtype=float)


class GradientBoostForecaster(Forecaster):
    """Global gradient-boosted model over lag features.

    Predicts the log ratio of next week's price to this week's, then converts
    back. Predicting a ratio rather than a level is what lets one model serve a
    catalog spanning five orders of magnitude in price.
    """

    name = "gbm"

    def __init__(self, categories: list[str] | None = None, random_state: int = 20240815):
        self.categories = categories or []
        self.category_index = {c: i for i, c in enumerate(self.categories)}
        self.random_state = random_state
        self.model = None

    def _row(self, series: PriceSeries, t: int, meta: dict) -> np.ndarray | None:
        return build_features(
            series.medians, series.dispersions, t,
            meta.get("lifecycle_status", "Active"),
            self.category_index.get(meta.get("category", ""), -1),
            len(self.categories),
        )

    def fit(self, dataset: list[tuple[PriceSeries, dict]], up_to: int) -> "GradientBoostForecaster":
        """Train on every (part, week) pair whose target week is <= `up_to`.

        `up_to` is the last week the caller is allowed to have seen. The target
        for a row at week t is week t+1, so rows are only admitted when
        t + 1 <= up_to. That single condition is what keeps the walk-forward
        split honest.
        """
        from sklearn.ensemble import GradientBoostingRegressor

        features: list[np.ndarray] = []
        targets: list[float] = []

        for series, meta in dataset:
            medians = series.medians
            limit = min(up_to, len(medians) - 1)
            for t in range(max(LAGS), limit):
                row = self._row(series, t, meta)
                if row is None:
                    continue
                nxt = medians[t + 1]
                if nxt <= 0 or medians[t] <= 0:
                    continue
                features.append(row)
                targets.append(float(np.log(nxt / medians[t])))

        if not features:
            self.model = None
            return self

        self.model = GradientBoostingRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=3,
            subsample=0.9,
            random_state=self.random_state,
        )
        self.model.fit(np.vstack(features), np.array(targets))
        return self

    def forecast(self, series: PriceSeries, context: dict | None = None) -> ForecastResult:
        meta = context or {}
        if self.model is None or len(series) <= max(LAGS):
            return ForecastResult(self.name, series.last_median, {"fallback": "naive"})

        row = self._row(series, len(series) - 1, meta)
        if row is None:
            return ForecastResult(self.name, series.last_median, {"fallback": "naive"})

        log_ratio = float(self.model.predict(row.reshape(1, -1))[0])
        # Clamp: a boosted tree extrapolating badly should not print a 40x
        # forecast on a quote sheet.
        log_ratio = float(np.clip(log_ratio, -1.5, 1.5))
        predicted = series.last_median * float(np.exp(log_ratio))
        return ForecastResult(self.name, predicted, {"log_ratio": round(log_ratio, 4)})


def build_forecasters(categories: list[str] | None = None,
                      cache: OrderCache | None = None) -> dict[str, Forecaster]:
    return {
        NaiveForecaster.name: NaiveForecaster(),
        SarimaForecaster.name: SarimaForecaster(cache=cache),
        GradientBoostForecaster.name: GradientBoostForecaster(categories=categories),
    }
