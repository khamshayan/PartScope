"""Tests for AS6171 test-flow routing.

These rules exist to be audited, so the tests check the arithmetic and the
explanation, not just the band. A recommendation whose reasons do not add up to
its score is worse than no recommendation -- it looks defensible and is not.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.risk import rules
from app.risk.rules import assess, assess_row, band_for

TODAY = date(2026, 8, 12)


def make(**overrides):
    """A current, plentiful, calm, confidently matched jellybean part."""
    base = dict(
        lifecycle_status="Active",
        authorized_stock=25_000,
        volatility_flag="STABLE",
        is_defense_grade=False,
        category="Fixed Resistors",
        date_introduced="2021-03-04",
        confidence=1.0,
        today=TODAY,
    )
    base.update(overrides)
    return assess(**base)


def codes(assessment) -> set[str]:
    return {reason.code for reason in assessment.reasons}


class TestIndividualRules:
    @pytest.mark.parametrize("status,expected", [
        ("Obsolete", rules.POINTS_OBSOLETE),
        ("EOL", rules.POINTS_OBSOLETE),
        ("NRND", 0),
        ("Active", 0),
    ])
    def test_lifecycle(self, status, expected):
        assert make(lifecycle_status=status).score == expected

    def test_zero_authorized_stock(self):
        assert make(authorized_stock=0).score == rules.POINTS_NO_AUTHORIZED_STOCK

    def test_missing_stock_is_not_treated_as_zero(self):
        """None is missing data. Scoring it as "no stock" invents a finding."""
        assert make(authorized_stock=None).score == 0

    @pytest.mark.parametrize("flag,expected", [
        ("VOLATILE", rules.POINTS_VOLATILE),
        ("ELEVATED", rules.POINTS_ELEVATED),
        ("STABLE", 0),
        (None, 0),
    ])
    def test_market_heat(self, flag, expected):
        assert make(volatility_flag=flag).score == expected

    def test_defense_grade_flag(self):
        assert make(is_defense_grade=True).score == rules.POINTS_DEFENSE_GRADE

    def test_defense_category_counts_even_without_the_flag(self):
        result = make(is_defense_grade=False, category="MIL-Spec Circular Connectors")
        assert result.score == rules.POINTS_DEFENSE_GRADE

    def test_age_threshold(self):
        assert make(date_introduced="2000-01-01").score == rules.POINTS_AGED
        # 15 years exactly is not "more than 15 years".
        assert make(date_introduced="2011-08-13").score == 0

    def test_unparseable_date_scores_nothing_rather_than_guessing(self):
        assert make(date_introduced="sometime in the nineties").score == 0
        assert make(date_introduced=None).score == 0

    def test_low_confidence(self):
        assert make(confidence=0.79).score == rules.POINTS_LOW_CONFIDENCE
        assert make(confidence=0.8).score == 0


class TestBands:
    @pytest.mark.parametrize("score,title", [
        (0, "Standard"), (25, "Standard"),
        (26, "Enhanced"), (55, "Enhanced"),
        (56, "Full AS6171"), (100, "Full AS6171"),
    ])
    def test_boundaries(self, score, title):
        assert band_for(score).title == title

    def test_each_band_adds_to_the_one_before(self):
        standard = set(rules.STANDARD.methods)
        enhanced = set(rules.ENHANCED.methods)
        full = set(rules.FULL.methods)
        assert standard < enhanced < full

    def test_estimates_rise_with_depth(self):
        assert rules.STANDARD.estimated_hours < rules.ENHANCED.estimated_hours
        assert rules.ENHANCED.estimated_hours < rules.FULL.estimated_hours

    def test_full_flow_approaches_the_48_hour_target(self):
        assert rules.FULL.approaching_target
        assert not rules.FULL.exceeds_target
        assert not rules.STANDARD.approaching_target


class TestScoringIsAuditable:
    def test_reasons_always_sum_to_the_score(self):
        result = make(
            lifecycle_status="Obsolete", authorized_stock=0,
            volatility_flag="VOLATILE", is_defense_grade=True,
            date_introduced="2004-01-01", confidence=0.6,
        )
        assert sum(reason.points for reason in result.reasons) == result.score

    def test_a_clean_part_has_no_reasons_at_all(self):
        result = make()
        assert result.reasons == []
        assert result.score == 0

    def test_every_reason_carries_a_label_and_an_explanation(self):
        result = make(lifecycle_status="Obsolete", authorized_stock=0)
        for reason in result.reasons:
            assert reason.label, "a reason with no label cannot be read"
            assert reason.detail, f"{reason.code} has no explanation"
            assert reason.points > 0

    def test_the_weights_can_reach_but_not_exceed_100(self):
        worst = make(
            lifecycle_status="Obsolete", authorized_stock=0,
            volatility_flag="VOLATILE", is_defense_grade=True,
            date_introduced="1998-06-01", confidence=0.5,
        )
        assert worst.score == 100


class TestAcceptanceCases:
    """The two cases the brief names explicitly."""

    def test_obsolete_defense_part_with_a_volatile_price_gets_full_as6171(self):
        result = make(
            lifecycle_status="Obsolete",
            authorized_stock=0,
            volatility_flag="VOLATILE",
            is_defense_grade=True,
            category="MIL-Spec Circular Connectors",
            date_introduced="2003-05-20",
            confidence=1.0,
        )

        assert result.recommendation.title == "Full AS6171"
        # 30 + 20 + 15 + 15 + 10 = 90
        assert result.score == 90
        assert codes(result) == {
            "lifecycle", "no_authorized_stock", "market_volatile",
            "defense_grade", "aged_part",
        }
        assert "Radiological (X-ray) inspection" in result.recommendation.methods
        assert "Decapsulation / internal physical analysis" in result.recommendation.methods
        assert result.recommendation.estimated_hours == 36

    def test_active_jellybean_part_gets_standard(self):
        result = make()
        assert result.recommendation.title == "Standard"
        assert result.score == 0
        assert result.recommendation.methods == [
            "External visual inspection (EVI)",
            "Marking permanency test",
        ]
        assert result.recommendation.estimated_hours == 4


class TestAssessRow:
    def test_reads_an_analyze_row(self):
        row = {
            "matched_mpn": "PSMN10V4-60YL",
            "lifecycle_status": "Obsolete",
            "authorized_stock": 0,
            "volatility_flag": "VOLATILE",
            "is_defense_grade": False,
            "category": "Power FETs",
            "date_introduced": "2015-01-01",
            "confidence": 1.0,
        }
        result = assess_row(row, today=TODAY)
        assert result is not None
        assert result.score == 65  # 30 + 20 + 15
        assert result.recommendation.title == "Full AS6171"

    def test_an_unidentified_line_gets_no_recommendation(self):
        """You cannot route a test flow for a part you have not identified.

        The honest answer is "identify it first", not a score built from the
        single signal that happens to exist (low confidence).
        """
        assert assess_row({"matched_mpn": None, "confidence": 0.0}, today=TODAY) is None

    def test_serialises_for_the_api(self):
        row = {
            "matched_mpn": "X", "lifecycle_status": "EOL", "authorized_stock": 0,
            "volatility_flag": "ELEVATED", "is_defense_grade": False,
            "category": "Operational Amplifiers", "date_introduced": "2019-01-01",
            "confidence": 1.0,
        }
        payload = assess_row(row, today=TODAY).to_dict()
        assert payload["risk_score"] == 58
        assert payload["test_recommendation"] == "Full AS6171"
        assert payload["est_turnaround_hours"] == 36
        assert payload["target_turnaround_hours"] == 48
        assert payload["approaching_target"] is True
        assert len(payload["risk_reasons"]) == 3
        assert all("points" in reason for reason in payload["risk_reasons"])
