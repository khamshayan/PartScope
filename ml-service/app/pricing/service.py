"""One call per part: price band, forecast, and market heat.

Holds the process-wide caches. The SARIMA order cache is the reason a warm
forecast comes back in milliseconds instead of seconds -- order selection runs a
27-candidate grid search, and its answer is stable enough to reuse.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import repository, volatility
from .forecasters import (
    ForecastResult,
    GradientBoostForecaster,
    NaiveForecaster,
    OrderCache,
    SarimaForecaster,
)

DEFAULT_MODEL = "sarima"


@dataclass
class PricingSnapshot:
    mpn: str
    price_p25: float
    price_p50: float
    price_p75: float
    quote_count: int
    forecast: ForecastResult
    heat: volatility.VolatilityAssessment
    weeks_of_history: int

    def to_dict(self) -> dict:
        payload = {
            "matched_mpn": self.mpn,
            "price_p25": round(self.price_p25, 4),
            "price_p50": round(self.price_p50, 4),
            "price_p75": round(self.price_p75, 4),
            "quote_count": self.quote_count,
            "weeks_of_history": self.weeks_of_history,
        }
        payload.update(self.forecast.to_dict())
        payload.update(self.heat.to_dict())
        return payload


def empty_snapshot(mpn: str) -> PricingSnapshot:
    """What to return for a part with no price history.

    A no-match line, or a part nobody has quoted, is a legitimate result. It
    must render as "no data", never as a price of zero -- a zero in a price
    column is a number someone might act on.
    """
    return PricingSnapshot(
        mpn=mpn,
        price_p25=0.0,
        price_p50=0.0,
        price_p75=0.0,
        quote_count=0,
        forecast=ForecastResult("none", 0.0, {"reason": "no price history"}),
        heat=volatility.VolatilityAssessment(1.0, volatility.STABLE, 0.0, 0.0, 0, []),
        weeks_of_history=0,
    )


class PricingService:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.order_cache = OrderCache()
        self.forecasters = {
            "naive": NaiveForecaster(),
            "sarima": SarimaForecaster(cache=self.order_cache),
            "gbm": GradientBoostForecaster(),
        }
        self.default_model = model
        self._series_cache: dict[str, repository.PriceSeries] = {}

    def warm(self, mpns: list[str]) -> None:
        """Preload several parts' series in one round trip.

        An RFQ is a batch of lines; fetching each part's history separately
        turns one query into twenty.
        """
        missing = [m for m in mpns if m not in self._series_cache]
        if missing:
            self._series_cache.update(repository.fetch_series(missing))

    def series_for(self, mpn: str) -> repository.PriceSeries:
        if mpn not in self._series_cache:
            self._series_cache.update(repository.fetch_series([mpn]))
        return self._series_cache.get(mpn, repository.PriceSeries(mpn, []))

    def snapshot(self, mpn: str, model: str | None = None,
                 context: dict | None = None) -> PricingSnapshot:
        series = self.series_for(mpn)
        if not len(series):
            return empty_snapshot(mpn)

        p25, p50, p75, quotes = repository.fetch_band(
            mpn, weeks=volatility.CURRENT_WINDOW_WEEKS
        )
        heat = volatility.assess(series)

        name = model or self.default_model
        forecaster = self.forecasters.get(name, self.forecasters["naive"])
        forecast = forecaster.forecast(series, context)

        return PricingSnapshot(
            mpn=mpn,
            price_p25=p25,
            price_p50=p50,
            price_p75=p75,
            quote_count=quotes,
            forecast=forecast,
            heat=heat,
            weeks_of_history=len(series),
        )


_SERVICE: PricingService | None = None


def get_pricing_service() -> PricingService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PricingService()
    return _SERVICE
