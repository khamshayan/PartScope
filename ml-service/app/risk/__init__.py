"""Rule-based AS6171 test-flow routing. Transparent by construction."""

from .rules import (
    ENHANCED,
    FULL,
    STANDARD,
    TARGET_TURNAROUND_HOURS,
    RiskAssessment,
    RiskReason,
    TestRecommendation,
    assess,
    assess_row,
    band_for,
)

__all__ = [
    "assess", "assess_row", "band_for",
    "RiskAssessment", "RiskReason", "TestRecommendation",
    "STANDARD", "ENHANCED", "FULL", "TARGET_TURNAROUND_HOURS",
]
