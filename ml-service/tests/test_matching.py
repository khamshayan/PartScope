"""Accuracy evaluation for the part matcher.

Runs all 60 hand-written cases and reports top-1 accuracy, top-3 accuracy and
the false-positive rate on the ten deliberately non-existent parts. Those three
numbers go in the README, so this file is what makes that claim checkable.

Run just this report:
    pytest ml-service/tests/test_matching.py -s -k accuracy
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from app.matching.normalize import (
    confusable_variants,
    normalize,
    rule_normalize_separators,
    rule_split_grade_suffix,
    rule_strip_distributor_prefix,
    rule_strip_packaging_suffix,
    rule_strip_quantity_artefacts,
    rule_uppercase_and_collapse,
)

# The bar the brief sets for Phase 1. Asserted, not just printed, so a
# regression in the rules fails the build rather than quietly degrading.
MIN_TOP1_ACCURACY = 0.80
MAX_FALSE_POSITIVE_RATE = 0.30


# ---------------------------------------------------------------------------
# The normalization rules, one at a time
# ---------------------------------------------------------------------------

class TestNormalizationRules:
    """Each rule is tested alone, so a failure names the rule that broke."""

    @pytest.mark.parametrize("raw,expected", [
        ("stm32f103c8t6", "STM32F103C8T6"),
        ("  padded  ", "PADDED"),
        ("a   b\tc", "A B C"),
    ])
    def test_uppercase_and_collapse(self, raw, expected):
        assert rule_uppercase_and_collapse(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("STM32F103C8T6 X100", "STM32F103C8T6"),
        ("STM32F103C8T6 QTY 500", "STM32F103C8T6"),
        ("STM32F103C8T6 (250)", "STM32F103C8T6"),
        ("STM32F103C8T6, 500 PCS", "STM32F103C8T6"),
        # Must not eat a quantity-looking tail that is part of the number.
        ("BZX84-C10,215", "BZX84-C10,215"),
    ])
    def test_strip_quantity_artefacts(self, raw, expected):
        assert rule_strip_quantity_artefacts(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("STM32F103C8T6-TR", "STM32F103C8T6"),
        ("STM32F103C8T6/REEL", "STM32F103C8T6"),
        ("TLC2084IDGKR-ND", "TLC2084IDGKR"),
        ("1N4749RLG-TR13", "1N4749RLG"),
        # Stacked suffixes.
        ("AD2736BRZ-TR-ND", "AD2736BRZ"),
        # Must not strip so much that nothing identifiable remains.
        ("AB-TR", "AB-TR"),
    ])
    def test_strip_packaging_suffix(self, raw, expected):
        assert rule_strip_packaging_suffix(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("296-STM32F103C8T6", "STM32F103C8T6"),
        ("71-CRCW0603100KJKEB", "CRCW0603100KJKEB"),
        # A leading number that is part of the identity must survive.
        ("74HCT2G00PW", "74HCT2G00PW"),
        ("1N4749RLG", "1N4749RLG"),
    ])
    def test_strip_distributor_prefix(self, raw, expected):
        assert rule_strip_distributor_prefix(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("BZX84-C5V6", "BZX84C5V6"),
        ("MCP645-I/SN", "MCP645ISN"),
        ("D38999 / 26JC124SN", "D3899926JC124SN"),
    ])
    def test_normalize_separators(self, raw, expected):
        assert rule_normalize_separators(raw) == expected

    def test_split_grade_suffix(self):
        base, grade = rule_split_grade_suffix("STM32F103C8T6")
        assert base == "STM32F103C8" and grade == "T6"

    def test_split_grade_suffix_leaves_short_keys_alone(self):
        # Over-trimming a short key turns a precise input into a vague prefix.
        assert rule_split_grade_suffix("LM358") == ("LM358", "")

    def test_confusable_variants_are_non_destructive(self):
        """O/0 handling must offer readings, never overwrite the input."""
        variants = confusable_variants("STM32F1O3")
        assert "STM32F103" in variants
        assert "STM32F1O3" not in variants, "the original must not be in its own variants"

    def test_confusable_variants_bail_out_when_ambiguity_explodes(self):
        # A key that is mostly confusable characters would generate a
        # combinatorial spray of candidates worth nothing.
        assert confusable_variants("O0I1O0I1O0I1") == []

    def test_pipeline_records_what_it_did(self):
        result = normalize("296-STM32F151F8U6-TR")
        assert result.key == "STM32F151F8U6"
        assert "strip_packaging_suffix" in result.applied
        assert "strip_distributor_prefix" in result.applied
        assert result.original == "296-STM32F151F8U6-TR"


# ---------------------------------------------------------------------------
# Ground truth integrity
# ---------------------------------------------------------------------------

def test_every_expected_mpn_exists_in_catalog(catalog_index, test_cases):
    """Guards against the evaluation set rotting when the generator changes.

    A test set with wrong ground truth is worse than no test set: it reports a
    number that means nothing. This caught eight stale cases when the catalog
    was regenerated.
    """
    known = {p.mpn for p in catalog_index.parts}
    missing = [
        f"#{c['id']} expects {c['expected_mpn']!r}"
        for c in test_cases
        if c["expected_mpn"] is not None and c["expected_mpn"] not in known
    ]
    assert not missing, "test cases reference parts that are not in the catalog: " + "; ".join(missing)


def test_nonexistent_cases_are_really_absent(catalog_index, test_cases):
    """The false-positive cases must not accidentally be real parts."""
    offenders = []
    for case in test_cases:
        if case["expected_mpn"] is not None:
            continue
        key = rule_normalize_separators(case["input"].upper())
        if key in catalog_index.by_key:
            offenders.append(f"#{case['id']} {case['input']!r}")
    assert not offenders, "cases marked nonexistent are in the catalog: " + "; ".join(offenders)


def test_case_set_is_complete(test_cases):
    assert len(test_cases) == 60
    kinds = defaultdict(int)
    for case in test_cases:
        kinds[case["kind"]] += 1
    assert kinds["nonexistent"] == 10, "the brief requires ten no-match cases"
    # Every messy shape the brief calls out must be represented.
    for required in ("exact", "packaging_suffix", "truncation", "typo", "lowercase",
                     "whitespace", "distributor_prefix", "o0_confusion", "i1_confusion"):
        assert kinds[required] > 0, f"no cases cover {required}"


# ---------------------------------------------------------------------------
# The accuracy report
# ---------------------------------------------------------------------------

def _evaluate(matcher, test_cases) -> dict:
    real = [c for c in test_cases if c["expected_mpn"] is not None]
    fake = [c for c in test_cases if c["expected_mpn"] is None]

    by_kind: dict[str, dict[str, int]] = defaultdict(
        lambda: {"n": 0, "top1": 0, "top3": 0}
    )
    top1 = top3 = 0
    misses: list[tuple[dict, str | None]] = []

    for case in real:
        result = matcher.match(case["input"])
        ranked = [c.part.mpn for c in result.candidates]
        expected = case["expected_mpn"]

        hit1 = bool(ranked) and ranked[0] == expected
        hit3 = expected in ranked[:3]

        top1 += hit1
        top3 += hit3
        bucket = by_kind[case["kind"]]
        bucket["n"] += 1
        bucket["top1"] += hit1
        bucket["top3"] += hit3
        if not hit1:
            misses.append((case, ranked[0] if ranked else None))

    false_positives = []
    for case in fake:
        result = matcher.match(case["input"])
        bucket = by_kind["nonexistent"]
        bucket["n"] += 1
        if result.matched:
            false_positives.append((case, result.best.part.mpn, result.confidence))
        else:
            bucket["top1"] += 1
            bucket["top3"] += 1

    return {
        "n_real": len(real),
        "n_fake": len(fake),
        "top1": top1,
        "top3": top3,
        "top1_accuracy": top1 / len(real) if real else 0.0,
        "top3_accuracy": top3 / len(real) if real else 0.0,
        "false_positives": false_positives,
        "false_positive_rate": len(false_positives) / len(fake) if fake else 0.0,
        "by_kind": dict(by_kind),
        "misses": misses,
    }


def test_matcher_accuracy(matcher, test_cases, capsys):
    report = _evaluate(matcher, test_cases)

    with capsys.disabled():
        print("\n")
        print("=" * 68)
        print("  PART MATCHER ACCURACY".ljust(68))
        print("=" * 68)
        print(f"  evaluation set      {len(test_cases)} hand-written cases "
              f"({report['n_real']} real, {report['n_fake']} non-existent)")
        print()
        print(f"  top-1 accuracy      {report['top1_accuracy']:6.1%}   "
              f"({report['top1']}/{report['n_real']})")
        print(f"  top-3 accuracy      {report['top3_accuracy']:6.1%}   "
              f"({report['top3']}/{report['n_real']})")
        print(f"  false-positive rate {report['false_positive_rate']:6.1%}   "
              f"({len(report['false_positives'])}/{report['n_fake']} "
              f"non-existent parts matched anyway)")
        print()
        print("  " + "-" * 64)
        print(f"  {'case kind':<22}{'n':>4}{'top-1':>12}{'top-3':>12}")
        print("  " + "-" * 64)
        for kind in sorted(report["by_kind"], key=lambda k: (k == "nonexistent", k)):
            stats = report["by_kind"][kind]
            label = "no_match (correct)" if kind == "nonexistent" else ""
            print(f"  {kind:<22}{stats['n']:>4}"
                  f"{stats['top1'] / stats['n']:>11.0%}"
                  f"{stats['top3'] / stats['n']:>12.0%}  {label}")
        print("  " + "-" * 64)

        if report["misses"]:
            print("\n  top-1 misses:")
            for case, got in report["misses"]:
                print(f"    #{case['id']:>2} {case['kind']:<20} {case['input']!r}")
                print(f"        expected {case['expected_mpn']!r}, got {got!r}")

        if report["false_positives"]:
            print("\n  false positives (should have been no_match):")
            for case, got, conf in report["false_positives"]:
                print(f"    #{case['id']:>2} {case['input']!r} -> {got!r} @ {conf:.2f}")
        print("=" * 68)

    assert report["top1_accuracy"] >= MIN_TOP1_ACCURACY, (
        f"top-1 accuracy {report['top1_accuracy']:.1%} is below the "
        f"{MIN_TOP1_ACCURACY:.0%} bar"
    )
    assert report["false_positive_rate"] <= MAX_FALSE_POSITIVE_RATE, (
        f"false-positive rate {report['false_positive_rate']:.1%} exceeds "
        f"{MAX_FALSE_POSITIVE_RATE:.0%}"
    )
    assert report["top3_accuracy"] >= report["top1_accuracy"]


# ---------------------------------------------------------------------------
# Cascade behaviour
# ---------------------------------------------------------------------------

class TestCascadeTiers:
    def test_exact_match_is_full_confidence(self, matcher, catalog_index):
        mpn = catalog_index.parts[0].mpn
        result = matcher.match(mpn)
        assert result.method == "exact"
        assert result.confidence == 1.0

    def test_no_match_still_returns_near_misses(self, matcher):
        result = matcher.match("ZZQQ99DEFINITELYNOTAPART")
        assert not result.matched
        assert result.method == "no_match"
        assert 0 < len(result.near_misses) <= 3, (
            "a buyer needs something to eyeball, not an empty row"
        )

    def test_empty_input_is_handled(self, matcher):
        assert not matcher.match("   ").matched

    def test_confidence_is_ordered_by_tier(self, matcher, catalog_index):
        """Weaker evidence must never outrank stronger evidence."""
        exact = matcher.match(catalog_index.parts[0].mpn)
        fuzzy = matcher.match("MMSZ4744AT1F")
        assert exact.confidence > fuzzy.confidence

    def test_review_threshold_separates_fuzzy_from_the_rest(self, matcher, test_cases):
        """Everything below 0.7 -- the UI's Review tag -- should be a guess."""
        for case in test_cases:
            if case["expected_mpn"] is None:
                continue
            result = matcher.match(case["input"])
            if result.matched and result.confidence < 0.7:
                assert result.method == "fuzzy", (
                    f"#{case['id']} {case['input']!r} is below the review "
                    f"threshold but matched via {result.method}"
                )


class TestAlternates:
    def test_alternates_are_cross_manufacturer_and_in_category(self, matcher, catalog_index):
        part = next(p for p in catalog_index.parts
                    if p.category == "Voltage Regulators")
        alternates = matcher.alternates.find(part)
        assert alternates, "a mainstream part should have alternates"
        for alt in alternates:
            assert alt["manufacturer"] != part.manufacturer
            assert alt["mpn"] != part.mpn

    def test_alternates_are_capped_at_five(self, matcher, catalog_index):
        part = next(p for p in catalog_index.parts if p.category == "Fixed Resistors")
        assert len(matcher.alternates.find(part)) <= 5

    def test_obsolete_alternates_rank_below_equivalent_active_ones(self, matcher, catalog_index):
        """Swapping one sourcing problem for another is not a suggestion."""
        part = next(p for p in catalog_index.parts
                    if p.category == "Operational Amplifiers" and not p.is_obsolete)
        alternates = matcher.alternates.find(part, limit=5)
        statuses = [a["lifecycle_status"] in ("Obsolete", "EOL") for a in alternates]
        # Active suggestions should not appear after obsolete ones.
        assert statuses == sorted(statuses), (
            f"obsolete alternates ranked above active ones: {alternates}"
        )

    def test_similarity_is_reported_as_higher_is_better(self, matcher, catalog_index):
        part = next(p for p in catalog_index.parts if p.category == "Power FETs")
        alternates = matcher.alternates.find(part)
        scores = [a["similarity"] for a in alternates]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 < s <= 1.0 for s in scores)
