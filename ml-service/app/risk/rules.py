"""How much counterfeit testing does this part warrant?

Rules, not a model, and that is the whole point. A buyer is being asked to
spend real money and up to a day and a half of lab time on the strength of this
number, and their supplier will argue about it. "The gradient booster said 0.83"
does not survive that conversation. "Obsolete +30, no authorised stock +20,
volatile market +15, defense grade +15 = 80, which crosses 56" does.

So every score carries the itemised reasons that produced it, and each rule
below maps to one line a human can check or dispute. Nothing here is learned,
nothing is hidden, and the arithmetic is visible in the UI.

The test methods reference AS6171, the SAE standard for counterfeit electronic
part test methods. The banding -- which methods at which risk level -- is a
judgement call modelled on how distributors actually triage; the standard
defines the methods, not the thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

# Rule weights. Named constants because these are exactly the numbers someone
# will want to argue with, and they should be arguable in one place.
POINTS_OBSOLETE = 30
POINTS_NO_AUTHORIZED_STOCK = 20
POINTS_VOLATILE = 15
POINTS_ELEVATED = 8
POINTS_DEFENSE_GRADE = 15
POINTS_AGED = 10
POINTS_LOW_CONFIDENCE = 10

AGE_THRESHOLD_YEARS = 15
LOW_CONFIDENCE_THRESHOLD = 0.8

# Band upper bounds, inclusive.
STANDARD_MAX = 25
ENHANCED_MAX = 55

# The turnaround a buyer is usually working against.
TARGET_TURNAROUND_HOURS = 48
# Flag when an estimate reaches this share of the target.
APPROACHING_TARGET = 0.7

DEAD_LIFECYCLES = {"Obsolete", "EOL"}


@dataclass(frozen=True)
class RiskReason:
    """One rule that fired, with the points it contributed."""

    code: str
    label: str
    points: int
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "points": self.points,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class TestRecommendation:
    band: str
    title: str
    methods: list[str]
    estimated_hours: int

    @property
    def approaching_target(self) -> bool:
        return self.estimated_hours >= TARGET_TURNAROUND_HOURS * APPROACHING_TARGET

    @property
    def exceeds_target(self) -> bool:
        return self.estimated_hours > TARGET_TURNAROUND_HOURS


# Each band ADDS to the one before it -- that is how AS6171 triage actually
# works, and stating the added methods separately keeps that visible.
STANDARD = TestRecommendation(
    band="standard",
    title="Standard",
    methods=[
        "External visual inspection (EVI)",
        "Marking permanency test",
    ],
    estimated_hours=4,
)

ENHANCED = TestRecommendation(
    band="enhanced",
    title="Enhanced",
    methods=[
        "External visual inspection (EVI)",
        "Marking permanency test",
        "XRF elemental analysis",
        "Dimensional analysis",
        "Solderability test",
    ],
    estimated_hours=12,
)

FULL = TestRecommendation(
    band="full",
    title="Full AS6171",
    methods=[
        "External visual inspection (EVI)",
        "Marking permanency test",
        "XRF elemental analysis",
        "Dimensional analysis",
        "Solderability test",
        "Radiological (X-ray) inspection",
        "Decapsulation / internal physical analysis",
        "Electrical parameter testing",
    ],
    estimated_hours=36,
)


@dataclass
class RiskAssessment:
    score: int
    recommendation: TestRecommendation
    reasons: list[RiskReason]

    def to_dict(self) -> dict:
        return {
            "risk_score": self.score,
            "test_recommendation": self.recommendation.title,
            "test_band": self.recommendation.band,
            "test_methods": list(self.recommendation.methods),
            "est_turnaround_hours": self.recommendation.estimated_hours,
            "target_turnaround_hours": TARGET_TURNAROUND_HOURS,
            "approaching_target": self.recommendation.approaching_target,
            "exceeds_target": self.recommendation.exceeds_target,
            "risk_reasons": [reason.to_dict() for reason in self.reasons],
        }


def band_for(score: int) -> TestRecommendation:
    if score <= STANDARD_MAX:
        return STANDARD
    if score <= ENHANCED_MAX:
        return ENHANCED
    return FULL


def _years_since(introduced: str | date | None, today: date) -> float | None:
    if not introduced:
        return None
    if isinstance(introduced, datetime):
        introduced = introduced.date()
    if isinstance(introduced, str):
        try:
            introduced = date.fromisoformat(introduced[:10])
        except ValueError:
            return None
    if not isinstance(introduced, date):
        return None
    return (today - introduced).days / 365.25


def assess(
    *,
    lifecycle_status: str | None,
    authorized_stock: int | None,
    volatility_flag: str | None,
    is_defense_grade: bool | None,
    category: str | None,
    date_introduced: str | date | None,
    confidence: float,
    today: date | None = None,
) -> RiskAssessment:
    """Score a line item and explain the score.

    Every argument is keyword-only: six similar-looking values, several of them
    optional, is precisely the signature where a positional call silently swaps
    two arguments and the score is quietly wrong ever after.
    """
    today = today or datetime.now(timezone.utc).date()
    reasons: list[RiskReason] = []

    if lifecycle_status in DEAD_LIFECYCLES:
        reasons.append(RiskReason(
            "lifecycle", f"{lifecycle_status}", POINTS_OBSOLETE,
            "Out of production; the only supply is what is already in the channel.",
        ))

    # Only meaningful alongside a dead lifecycle or a real stock figure; a null
    # stock reading is missing data, not a zero.
    if authorized_stock is not None and authorized_stock <= 0:
        reasons.append(RiskReason(
            "no_authorized_stock", "No authorized stock", POINTS_NO_AUTHORIZED_STOCK,
            "No franchised distributor inventory, so any offer is broker stock.",
        ))

    if volatility_flag == "VOLATILE":
        reasons.append(RiskReason(
            "market_volatile", "Volatile market", POINTS_VOLATILE,
            "Broker quotes are far apart, which is when counterfeit supply appears.",
        ))
    elif volatility_flag == "ELEVATED":
        reasons.append(RiskReason(
            "market_elevated", "Elevated market", POINTS_ELEVATED,
            "Quote dispersion is above this part's own baseline.",
        ))

    defense = bool(is_defense_grade) or "MIL-Spec" in (category or "")
    if defense:
        reasons.append(RiskReason(
            "defense_grade", "Defense / aerospace grade", POINTS_DEFENSE_GRADE,
            "Consequence of a counterfeit escape is severe in this application.",
        ))

    age = _years_since(date_introduced, today)
    if age is not None and age > AGE_THRESHOLD_YEARS:
        reasons.append(RiskReason(
            "aged_part", f"Introduced {age:.0f} years ago", POINTS_AGED,
            f"Older than {AGE_THRESHOLD_YEARS} years; more time in the grey market.",
        ))

    if confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.append(RiskReason(
            "low_confidence", "Uncertain part match", POINTS_LOW_CONFIDENCE,
            f"Matched at {confidence:.0%}; confirm the part number before testing.",
        ))

    # Capped at 100 so the number stays on the stated scale. The rules sum to
    # exactly 100 today, so this only matters if a weight is changed later.
    score = min(100, sum(reason.points for reason in reasons))
    return RiskAssessment(score, band_for(score), reasons)


def assess_row(row: dict, today: date | None = None) -> RiskAssessment | None:
    """Assess an /analyze result row.

    Returns None for a line that matched nothing. You cannot recommend a test
    depth for a part you have not identified -- the honest answer is "identify
    it first", not a score computed from the one signal that happens to exist.
    """
    if not row.get("matched_mpn"):
        return None

    return assess(
        lifecycle_status=row.get("lifecycle_status"),
        authorized_stock=row.get("authorized_stock"),
        volatility_flag=row.get("volatility_flag"),
        is_defense_grade=row.get("is_defense_grade"),
        category=row.get("category"),
        date_introduced=row.get("date_introduced"),
        confidence=float(row.get("confidence") or 0.0),
        today=today,
    )
