"""Run the forecaster backtest and write the results doc.

    python scripts/backtest.py                 # full run: 500 parts, 25 origins
    python scripts/backtest.py --parts 50      # quick pass while iterating

Writes:
    docs/backtest-results.md              tables and the derived observations
    ml-service/data/out/predictions.csv   every prediction, for the notebook
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ml-service"))

from app.pricing.backtest import (  # noqa: E402
    MODELS,
    BacktestConfig,
    BacktestReport,
    format_table,
    run_backtest,
)

OUT_DIR = REPO_ROOT / "ml-service" / "data" / "out"
RESULTS_DOC = REPO_ROOT / "docs" / "backtest-results.md"


def write_predictions_csv(report: BacktestReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mpn", "week_index", "model", "actual", "predicted",
                         "lifecycle", "category", "during_spike"])
        for p in report.predictions:
            writer.writerow([p.mpn, p.week_index, p.model, f"{p.actual:.6f}",
                             f"{p.predicted:.6f}", p.lifecycle, p.category,
                             int(p.during_spike)])


def _markdown_table(report: BacktestReport) -> str:
    segments = report.segments()
    labels = {
        "overall": "All parts",
        "active": "Active / NRND",
        "obsolete": "Obsolete / EOL",
        "during_spike": "During a shortage spike",
        "normal": "Normal weeks",
    }
    lines = ["| Segment | Model | n | MAPE | Median APE | RMSE ($) |",
             "|---|---|---:|---:|---:|---:|"]
    for name, per_model in segments.items():
        best = min(MODELS, key=lambda m: per_model[m]["mape"])
        for model in MODELS:
            stats = per_model[model]
            mark = " **←**" if model == best else ""
            lines.append(
                f"| {labels[name]} | `{model}` | {stats['n']:,} | "
                f"{stats['mape']:.1%} | {stats['median_ape']:.1%} | "
                f"{stats['rmse']:.4f}{mark} |"
            )
    return "\n".join(lines)


def _observations(report: BacktestReport) -> list[str]:
    """Facts derived from the run. The prose commentary is written by hand."""
    segments = report.segments()
    notes = []
    for name in ("overall", "active", "obsolete", "during_spike", "normal"):
        per_model = segments[name]
        ranked = sorted(MODELS, key=lambda m: per_model[m]["mape"])
        best, worst = ranked[0], ranked[-1]
        notes.append(
            f"- **{name}**: best `{best}` at {per_model[best]['mape']:.1%} MAPE; "
            f"worst `{worst}` at {per_model[worst]['mape']:.1%} "
            f"(n={per_model[best]['n']:,})"
        )
    tally = report.wins()
    total = sum(tally.values()) or 1
    notes.append("- **closest forecast per part-week**: " + ", ".join(
        f"`{m}` {c / total:.0%}" for m, c in sorted(tally.items(), key=lambda kv: -kv[1])
    ))

    seasonal = sum(1 for v in report.sarima_orders.values()
                   if v["seasonal_order"][3] != 0)
    notes.append(
        f"- **SARIMA order selection**: a seasonal term (s=52) minimised AIC for "
        f"{seasonal} of {len(report.sarima_orders)} parts "
        f"({seasonal / max(len(report.sarima_orders), 1):.0%})"
    )
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the forecasters")
    parser.add_argument("--parts", type=int, default=500)
    parser.add_argument("--origins", type=int, default=25)
    parser.add_argument("--train-weeks", type=int, default=78)
    parser.add_argument("--no-seasonal", action="store_true",
                        help="skip s=52 candidates in SARIMA order selection")
    args = parser.parse_args()

    config = BacktestConfig(
        n_parts=args.parts,
        n_origins=args.origins,
        initial_train_weeks=args.train_weeks,
        try_seasonal=not args.no_seasonal,
    )

    started = time.perf_counter()
    report = run_backtest(config)

    print()
    print(format_table(report))
    print()
    print(f"{report.parts_evaluated} parts, {len(report.predictions):,} predictions, "
          f"{report.elapsed_seconds:.0f}s")

    write_predictions_csv(report, OUT_DIR / "predictions.csv")
    (OUT_DIR / "sarima_orders.json").write_text(
        json.dumps(report.sarima_orders, indent=2), encoding="utf-8")

    summary = {
        "segments": report.segments(),
        "wins": report.wins(),
        "parts": report.parts_evaluated,
        "predictions": len(report.predictions),
        "elapsed_seconds": report.elapsed_seconds,
        "config": vars(config),
        "markdown_table": _markdown_table(report),
        "observations": _observations(report),
    }
    (OUT_DIR / "backtest_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nwrote {OUT_DIR / 'predictions.csv'}")
    print(f"wrote {OUT_DIR / 'backtest_summary.json'}")
    print(f"total {time.perf_counter() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
