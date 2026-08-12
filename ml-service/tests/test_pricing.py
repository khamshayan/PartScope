"""Tests for the pricing engine.

The important ones here are the leakage guards. A forecasting backtest that
quietly sees the future produces beautiful numbers that mean nothing, and the
failure is invisible unless something explicitly checks for it.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from app.pricing import volatility
from app.pricing.forecasters import (
    GradientBoostForecaster,
    NaiveForecaster,
    OrderCache,
    SarimaForecaster,
    build_features,
)
from app.pricing.repository import PriceSeries, WeeklyPoint


# ---------------------------------------------------------------------------
# Builders for synthetic series, so these tests need no database
# ---------------------------------------------------------------------------

def make_series(medians, spreads=None, mpn="TEST-PART") -> PriceSeries:
    spreads = spreads if spreads is not None else [0.06] * len(medians)
    start = date(2023, 1, 2)
    points = []
    for i, (median, spread) in enumerate(zip(medians, spreads)):
        half = median * spread / 2
        points.append(WeeklyPoint(
            week_start=start + timedelta(weeks=i),
            p25=median - half, p50=median, p75=median + half,
            quotes=3, lead_time_days=30.0,
        ))
    return PriceSeries(mpn, points)


class TestDispersion:
    def test_dispersion_ratio_is_spread_over_median(self):
        point = WeeklyPoint(date(2024, 1, 1), 8.0, 10.0, 12.0, 3, 30.0)
        assert point.dispersion_ratio == pytest.approx(0.4)

    def test_zero_median_does_not_divide_by_zero(self):
        point = WeeklyPoint(date(2024, 1, 1), 0.0, 0.0, 0.0, 0, 0.0)
        assert point.dispersion_ratio == 0.0


class TestHeatIndex:
    def test_stable_market_is_stable(self):
        series = make_series([10.0] * 60, spreads=[0.05] * 60)
        result = volatility.assess(series)
        assert result.flag == volatility.STABLE
        assert result.heat_index == pytest.approx(1.0, abs=0.05)

    def test_widening_spread_flags_volatile(self):
        # Calm for a year, then brokers stop agreeing.
        spreads = [0.05] * 52 + [0.40] * 8
        result = volatility.assess(make_series([10.0] * 60, spreads=spreads))
        assert result.flag == volatility.VOLATILE
        assert result.heat_index > volatility.VOLATILE_THRESHOLD

    def test_moderate_widening_flags_elevated(self):
        spreads = [0.05] * 52 + [0.08] * 8
        result = volatility.assess(make_series([10.0] * 60, spreads=spreads))
        assert result.flag == volatility.ELEVATED

    def test_heat_measures_disagreement_not_price_level(self):
        """A steadily climbing price with brokers in lockstep is NOT volatile.

        This is the property that would break if dispersion were computed by
        pooling quotes across the whole 8-week window: the trend would enter the
        spread and a smooth, predictable rise would read as chaos.
        """
        climbing = [10.0 * (1.06 ** i) for i in range(60)]   # +6% every week
        result = volatility.assess(make_series(climbing, spreads=[0.05] * 60))
        assert result.flag == volatility.STABLE, (
            f"a smooth price rise was flagged {result.flag} "
            f"(heat {result.heat_index:.2f}) - dispersion is picking up trend"
        )

    def test_degenerate_baseline_does_not_manufacture_heat(self):
        # Brokers agreeing to the cent give a ~0 baseline; dividing by it would
        # produce enormous heat from nothing.
        result = volatility.assess(make_series([10.0] * 60, spreads=[0.0] * 60))
        assert result.heat_index == 1.0
        assert result.flag == volatility.STABLE

    def test_sparkline_is_capped_at_52_weeks(self):
        result = volatility.assess(make_series([10.0] * 104))
        assert len(result.sparkline) == 52

    def test_as_of_hides_later_weeks(self):
        """The backtest asks what the flag would have said in the past."""
        spreads = [0.05] * 52 + [0.40] * 8
        series = make_series([10.0] * 60, spreads=spreads)
        before = volatility.assess(series, as_of=52)
        after = volatility.assess(series)
        assert before.flag == volatility.STABLE
        assert after.flag == volatility.VOLATILE

    @pytest.mark.parametrize("heat,expected", [
        (0.5, volatility.STABLE),
        (1.29, volatility.STABLE),
        (1.3, volatility.ELEVATED),
        (2.0, volatility.ELEVATED),
        (2.01, volatility.VOLATILE),
    ])
    def test_threshold_boundaries(self, heat, expected):
        assert volatility.classify(heat) == expected


class TestNaiveForecaster:
    def test_predicts_last_observed_median(self):
        series = make_series([1.0, 2.0, 3.0, 4.5])
        assert NaiveForecaster().forecast(series).predicted_price == pytest.approx(4.5)


class TestSarimaForecaster:
    def test_forecast_is_in_a_sane_range(self):
        """Regression guard: an order of (0,0,0) with no intercept forecasts
        exactly 0 in log space, i.e. $1.00 regardless of the real price."""
        rng = np.random.default_rng(7)
        medians = list(50.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 60))))
        result = SarimaForecaster(try_seasonal=False).forecast(make_series(medians))
        assert 25.0 < result.predicted_price < 100.0, (
            f"forecast {result.predicted_price} is nowhere near the ~$50 series"
        )

    def test_short_series_falls_back_rather_than_failing(self):
        result = SarimaForecaster(try_seasonal=False).forecast(make_series([3.0, 3.1, 3.2]))
        assert result.predicted_price > 0

    def test_order_cache_avoids_a_second_search(self):
        import time

        cache = OrderCache()
        forecaster = SarimaForecaster(cache=cache, try_seasonal=False)
        series = make_series([10.0 + 0.1 * i for i in range(60)])

        start = time.perf_counter()
        forecaster.select_order(series)
        cold = time.perf_counter() - start

        start = time.perf_counter()
        forecaster.select_order(series)
        warm = time.perf_counter() - start

        assert len(cache) == 1
        assert warm < cold / 5, f"cache did not help: cold {cold:.3f}s warm {warm:.3f}s"


class TestGradientBoostFeatures:
    def test_returns_none_without_enough_history(self):
        medians = np.array([1.0] * 5)
        assert build_features(medians, np.zeros(5), 4, "Active", 0, 3) is None

    def test_features_only_look_backwards(self):
        """Changing the future must not change the features for week t.

        If it did, every backtest number in the results doc would be fiction.
        """
        rng = np.random.default_rng(3)
        medians = 10.0 + np.cumsum(rng.normal(0, 0.1, 60))
        dispersions = np.full(60, 0.06)
        t = 40

        baseline = build_features(medians, dispersions, t, "Active", 1, 4)

        tampered = medians.copy()
        tampered[t + 1:] *= 5.0          # rewrite everything after week t
        tampered_disp = dispersions.copy()
        tampered_disp[t + 1:] = 0.9
        after = build_features(tampered, tampered_disp, t, "Active", 1, 4)

        np.testing.assert_allclose(baseline, after, err_msg="features leak future data")

    def test_lifecycle_and_category_are_one_hot(self):
        medians = np.full(40, 5.0)
        row = build_features(medians, np.full(40, 0.06), 30, "Obsolete", 2, 4)
        assert row is not None
        # Four lifecycle levels then four categories at the tail.
        assert list(row[-8:-4]) == [0.0, 0.0, 1.0, 0.0]
        assert list(row[-4:]) == [0.0, 0.0, 1.0, 0.0]

    def test_unfitted_model_falls_back_to_naive(self):
        gbm = GradientBoostForecaster(categories=["A"])
        series = make_series([2.0] * 30)
        assert gbm.forecast(series).predicted_price == pytest.approx(2.0)

    def test_fit_never_uses_a_target_beyond_the_origin(self):
        """The one condition that keeps the walk-forward split honest."""
        rng = np.random.default_rng(11)
        dataset = []
        for i in range(6):
            medians = list(10.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 60))))
            dataset.append((make_series(medians, mpn=f"P{i}"),
                            {"lifecycle_status": "Active", "category": "X"}))

        gbm = GradientBoostForecaster(categories=["X"])
        gbm.fit(dataset, up_to=40)
        before = gbm.model.predict(
            gbm._row(dataset[0][0], 30, dataset[0][1]).reshape(1, -1)
        )

        # Corrupt every week after the training cut-off and refit.
        for series, _ in dataset:
            for point in series.points[41:]:
                object.__setattr__(point, "p50", point.p50 * 100)

        retrained = GradientBoostForecaster(categories=["X"])
        retrained.fit(dataset, up_to=40)
        after = retrained.model.predict(
            retrained._row(dataset[0][0], 30, dataset[0][1]).reshape(1, -1)
        )
        np.testing.assert_allclose(before, after, rtol=1e-9,
                                   err_msg="training set reached past the origin")


class TestPricingServiceIntegration:
    """Against the real seeded database."""

    def test_snapshot_has_a_coherent_band(self, catalog_index):
        from app.pricing import get_pricing_service

        service = get_pricing_service()
        mpn = catalog_index.parts[0].mpn
        snapshot = service.snapshot(mpn)
        assert snapshot.price_p25 <= snapshot.price_p50 <= snapshot.price_p75
        assert snapshot.quote_count > 0
        assert snapshot.forecast.predicted_price > 0
        assert len(snapshot.heat.sparkline) == 52

    def test_unknown_part_serialises_absence_as_null_not_zero(self):
        """A price of 0.00 in a quote sheet is a number someone might act on.

        Worse, a green STABLE chip on a part we could not identify is a
        confident claim that happens to be false.
        """
        from app.pricing.service import empty_snapshot

        payload = empty_snapshot("NOT-A-REAL-PART").to_dict()
        for field in ("price_p25", "price_p50", "price_p75", "forecast_price",
                      "heat_index", "volatility_flag", "forecast_model"):
            assert payload[field] is None, f"{field} should be null, got {payload[field]!r}"
        assert payload["quote_count"] == 0
        assert payload["sparkline"] == []

    def test_warm_forecast_meets_the_latency_budget(self, catalog_index):
        import time

        from app.pricing import get_pricing_service

        service = get_pricing_service()
        mpn = catalog_index.parts[1].mpn
        service.snapshot(mpn)  # warm the order cache

        start = time.perf_counter()
        service.snapshot(mpn)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.4, f"warm forecast took {elapsed * 1000:.0f}ms, budget is 400ms"
