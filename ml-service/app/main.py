"""FastAPI service: matching, pricing and market heat.

Everything with a model or an algorithm in it lives behind this service. The
Express tier owns validation, persistence and orchestration; keeping that line
clean is what lets the whole analysis pipeline be exercised from pytest with no
web tier in the way.

A note on the default forecaster. SARIMA order selection is a grid search that
costs seconds per part, so it cannot run inside a request -- a ten-line RFQ
would take a minute. The gradient booster is trained once at startup and then
predicts in about a millisecond, which is why it serves interactive traffic.
SARIMA is still reachable with ?model=sarima; the first call for a given part
pays for its order search, and every call after that is cached. See
docs/backtest-results.md for which model actually wins where.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.matching import PartMatcher, load_from_mongo
from app.parsing import parse_spreadsheet, parse_text
from app.risk import assess_row
from app.pricing import repository
from app.pricing.forecasters import GradientBoostForecaster
from app.pricing.service import PricingService, empty_snapshot

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sourcescope")

# Parts used to train the serving gradient booster. It is a global model, so it
# needs breadth across categories and lifecycles rather than the whole catalog.
# 400 is a compute trade: sklearn's GradientBoostingRegressor is single-threaded
# and 800 parts took ~170s to fit, against ~85s here for materially the same
# accuracy. The fitted model is cached to disk, so only the first start pays it.
GBM_TRAINING_PARTS = 400

DEFAULT_MODEL = "gbm"
MAX_ITEMS_PER_REQUEST = 200


class ServiceState:
    """Everything expensive, built once."""

    def __init__(self) -> None:
        self.matcher: PartMatcher | None = None
        self.pricing: PricingService | None = None
        self.started_at = time.time()
        self.ready = False
        self.startup_error: str | None = None


state = ServiceState()


def _train_startup_forecaster(pricing: PricingService, catalog_size: int) -> None:
    """Load the cached gradient booster, or fit and cache one."""
    from app.config import get_settings, mongo_client
    from app.pricing import model_cache

    settings = get_settings()
    mpns = repository.stratified_sample(GBM_TRAINING_PARTS)

    client = mongo_client()
    try:
        docs = client[settings.mongo_db]["parts"].find(
            {"mpn": {"$in": mpns}},
            {"_id": 0, "mpn": 1, "lifecycle_status": 1, "category": 1},
        )
        metadata = {d["mpn"]: d for d in docs}
    finally:
        client.close()

    categories = sorted({m.get("category", "") for m in metadata.values()})
    fingerprint = model_cache.signature(
        catalog_size, GBM_TRAINING_PARTS, settings.seed, categories
    )

    cached = model_cache.load(fingerprint)
    if cached is not None:
        pricing.forecasters["gbm"] = cached
        log.info("loaded cached gbm (signature %s)", fingerprint)
        return

    log.info("training gbm on %d parts -- this takes ~90s and is cached afterwards",
             len(mpns))
    started = time.perf_counter()
    series = repository.fetch_series(mpns)
    dataset = [(s, metadata.get(mpn, {})) for mpn, s in series.items() if len(s) > 20]

    gbm = GradientBoostForecaster(categories=categories)
    # Train on every week available; this is the serving model, not a backtest,
    # so there is no origin to hold out.
    longest = max((len(s) for s, _ in dataset), default=0)
    gbm.fit(dataset, up_to=longest)

    pricing.forecasters["gbm"] = gbm
    model_cache.save(gbm, fingerprint, {
        "training_parts": len(dataset),
        "categories": len(categories),
        "fit_seconds": round(time.perf_counter() - started, 1),
    })
    log.info("trained gbm on %d parts across %d categories in %.0fs",
             len(dataset), len(categories), time.perf_counter() - started)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    started = time.perf_counter()
    try:
        log.info("loading catalog...")
        state.matcher = PartMatcher(load_from_mongo())
        log.info("catalog: %d parts", len(state.matcher.index))

        state.pricing = PricingService(model=DEFAULT_MODEL)
        _train_startup_forecaster(state.pricing, len(state.matcher.index))

        state.ready = True
        log.info("ready in %.1fs", time.perf_counter() - started)
    except Exception as exc:  # noqa: BLE001
        # Come up unhealthy rather than refusing to start: /health should be
        # able to tell the caller what is wrong.
        state.startup_error = str(exc)
        log.error("startup failed: %s", exc)
    yield


app = FastAPI(
    title="Source Scope ML service",
    description="Part matching, price bands, forecasts and market heat. "
                "All data is synthetic - see docs/data-sources.md.",
    version="0.3.0",
    lifespan=lifespan,
)

# The Vite dev server is a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AnalyzeItem(BaseModel):
    input_string: str = Field(..., min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=0)


class AnalyzeRequest(BaseModel):
    items: list[AnalyzeItem] = Field(..., min_length=1, max_length=MAX_ITEMS_PER_REQUEST)
    model: str | None = Field(default=None, description="naive | sarima | gbm")


class ParseRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, max_length=500_000)
    max_items: int = Field(default=MAX_ITEMS_PER_REQUEST, ge=1, le=1000)


SPREADSHEET_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm")


def _require_ready() -> None:
    if not state.ready:
        raise HTTPException(
            status_code=503,
            detail=state.startup_error or "service is still starting up",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if state.ready else "starting",
        "ready": state.ready,
        "error": state.startup_error,
        "catalog_parts": len(state.matcher.index) if state.matcher else 0,
        "uptime_seconds": round(time.time() - state.started_at, 1),
        "default_model": DEFAULT_MODEL,
        "data": "synthetic - see docs/data-sources.md",
    }


@app.post("/parse")
def parse(request: ParseRequest) -> dict:
    """Free-form text or an email body into matcher-ready line items."""
    return parse_text(request.raw_text, max_items=request.max_items).to_dict()


@app.post("/parse/file")
async def parse_file(
    file: UploadFile = File(...),
    max_items: int = Form(default=MAX_ITEMS_PER_REQUEST),
) -> dict:
    """An uploaded spreadsheet or text file into matcher-ready line items.

    Routing is by extension, then by content: a .xlsx really is a zip archive,
    so a text file misnamed .xlsx fails openpyxl rather than parsing as one, and
    falling back to the text parser gets more out of it than an error would.
    """
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    name = (file.filename or "").lower()
    looks_like_spreadsheet = name.endswith(SPREADSHEET_SUFFIXES) or payload[:2] == b"PK"

    if looks_like_spreadsheet:
        try:
            return parse_spreadsheet(payload, file.filename or "").to_dict()
        except Exception as exc:  # noqa: BLE001
            if name.endswith(SPREADSHEET_SUFFIXES):
                raise HTTPException(
                    status_code=400,
                    detail=f"could not read {file.filename!r} as a spreadsheet: {exc}",
                ) from exc

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = payload.decode("latin-1")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"{file.filename!r} is neither a spreadsheet nor readable text",
            ) from exc

    result = parse_text(text, max_items=max_items)
    result.diagnostics["filename"] = file.filename
    return result.to_dict()


@app.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict:
    """Match, price and risk-flag a batch of RFQ line items."""
    _require_ready()
    started = time.perf_counter()

    matched = [state.matcher.analyze(item.input_string) for item in request.items]

    # One round trip for every part in the batch rather than one per line.
    state.pricing.warm([m["matched_mpn"] for m in matched if m["matched_mpn"]])

    results = []
    for item, match in zip(request.items, matched):
        row: dict = dict(match)
        row["quantity"] = item.quantity

        mpn = match["matched_mpn"]
        if mpn:
            snapshot = state.pricing.snapshot(
                mpn,
                model=request.model,
                context={
                    "lifecycle_status": match.get("lifecycle_status"),
                    "category": match.get("category"),
                },
            )
        else:
            snapshot = empty_snapshot("", reason="no part matched")
        row.update(snapshot.to_dict())
        row["matched_mpn"] = mpn  # snapshot would otherwise blank it for no_match

        # Risk depends on both the match and the market, so it runs last.
        risk = assess_row(row)
        row.update(risk.to_dict() if risk else {
            "risk_score": None,
            "test_recommendation": None,
            "test_band": None,
            "test_methods": [],
            "est_turnaround_hours": None,
            "risk_reasons": [],
        })
        results.append(row)

    return {
        "items": results,
        "count": len(results),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "model": request.model or DEFAULT_MODEL,
    }


# MPNs legitimately contain slashes -- D38999/24JA103SN, MCP645-I/SN -- so the
# path has to swallow them rather than treating them as route separators.
@app.get("/part/{mpn:path}")
def part_detail(mpn: str, weeks: int = Query(default=52, ge=1, le=104)) -> dict:
    _require_ready()

    part = next((p for p in state.matcher.index.exact(
        _normalized(mpn)) if p.mpn.upper() == mpn.upper()), None)
    if part is None:
        candidates = state.matcher.index.exact(_normalized(mpn))
        part = candidates[0] if candidates else None
    if part is None:
        raise HTTPException(status_code=404,
                            detail=f"no catalog part matches {mpn!r}")

    series = state.pricing.series_for(part.mpn)
    snapshot = state.pricing.snapshot(
        part.mpn,
        context={"lifecycle_status": part.lifecycle_status, "category": part.category},
    )

    history = [
        {
            "week_start": point.week_start.isoformat(),
            "p25": round(point.p25, 4),
            "p50": round(point.p50, 4),
            "p75": round(point.p75, 4),
            "quotes": point.quotes,
            "lead_time_days": round(point.lead_time_days, 1),
            "dispersion_ratio": round(point.dispersion_ratio, 4),
        }
        for point in series.points[-weeks:]
    ]

    payload = part.to_dict()
    payload.update(snapshot.to_dict())
    payload["history"] = history
    payload["alternates"] = state.matcher.alternates.find(part)
    return payload


def _normalized(mpn: str) -> str:
    from app.matching.normalize import rule_normalize_separators

    return rule_normalize_separators(mpn.upper())
