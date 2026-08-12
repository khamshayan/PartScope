"""Turn a messy input string into something comparable against the catalog.

This file and `pricing/volatility.py` are where a reader should spend their
time, so the rules are written out one at a time rather than collapsed into one
clever regex. Each rule is an object with a name, applied in a fixed order, and
each records what it changed -- so when a match goes wrong you can see exactly
which rule ate the character that mattered.

The ordering is deliberate and not interchangeable:

  1. case/whitespace first, so every later rule can assume uppercase input
  2. packaging suffixes before separator stripping, because most of them are
     recognisable only while the leading '-' or '/' is still attached
  3. distributor prefixes after packaging, since a distributor SKU wraps the
     manufacturer part (296-STM32F103C8T6-ND has both ends to remove)
  4. separators last, producing the comparison key

Two things this pipeline deliberately does NOT do:

  * It does not destructively "correct" O/0 or I/1. A zero and the letter O are
    both legitimate characters in real part numbers, so guessing wrong loses a
    match that would otherwise be exact. Instead both readings are produced as
    candidate variants and the matcher tries each.
  * It does not throw away the original. Everything is kept for display, because
    a buyer needs to see what they typed next to what it matched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Rule vocabulary
# ---------------------------------------------------------------------------

# Packaging, reel and handling suffixes. These say how the part is delivered,
# not which part it is, so two inputs differing only here are the same part.
# Order matters within this list: longer forms must be tried before the shorter
# forms they contain, or '-TR' would eat the start of '-TR13'.
PACKAGING_SUFFIXES = [
    "T&R", "TAPEANDREEL", "TAPE&REEL",
    "-TAPEREEL", "/TAPEREEL", "-TAPE", "/TAPE",
    "-REEL", "/REEL", "-RL", "/RL",
    "-TR13", "/TR13", "TR13",
    "-TR7", "/TR7", "TR7",
    "-TRPBF", "-TR", "/TR", "_TR",
    "-CT", "/CT",
    "-DKR", "/DKR",
    "-E4", "/E4", "-E3", "/E3",
    "-ND", "/ND",
    "-BULK", "/BULK", "-TUBE", "/TUBE", "-TRAY", "/TRAY",
    "-CUT", "-CUTTAPE",
]

# Distributor order-code prefixes. Digi-Key writes 296-<part>-ND, Mouser writes
# 595-<part>; the numeric stub is the distributor's, not the manufacturer's.
DISTRIBUTOR_PREFIX_RE = re.compile(r"^(?:\d{2,4})-(?=[A-Z0-9]{3,})")

# Quantity artefacts that survive a copy-paste out of a spreadsheet or an email
# line: "QTY 500", "X100", "(100)", "500PCS".
QUANTITY_ARTEFACT_RE = re.compile(
    r"(?:^|[\s,;])(?:QTY|QUANTITY|PCS?|PIECES|EA|EACH|X)\s*[:=]?\s*\d+\s*$"
    r"|(?:^|[\s,;])\d+\s*(?:PCS?|PIECES|EA|EACH)\s*$"
    r"|\s*[(\[]\s*\d+\s*[)\]]\s*$"
    r"|\s+X\d+\s*$",
    re.IGNORECASE,
)

# A trailing temperature/package grade. On many families the last one or two
# characters select the operating range and the body -- STM32F103C8 'T6' is
# LQFP, industrial. Splitting it off lets a partial number still reach its base
# part, which is why truncated inputs are matchable at all.
GRADE_SUFFIX_RE = re.compile(
    r"^(?P<base>[A-Z0-9]{6,}?)"
    r"(?P<grade>(?:[TQUHIYZ]\d|[EICM]\d|-?\d{1,2}[EICM])?)$"
)

SEPARATORS_RE = re.compile(r"[\s\-_./\\,#]+")

# Characters that are routinely confused when a part number is read off a reel
# label, a photograph, or a fax. Each maps to the other readings a human might
# have meant.
CONFUSABLE = {
    "O": "0", "0": "O",
    "I": "1", "1": "I",
}


@dataclass
class NormalizedInput:
    """The result of the pipeline, carrying its own audit trail."""

    original: str
    display: str          # cleaned but still human-readable
    key: str              # separators removed; the primary comparison key
    base_key: str         # key with a trailing grade suffix removed
    variants: list[str]   # O/0 and I/1 readings of `key`, excluding `key`
    grade: str            # the grade suffix that was split off, if any
    applied: list[str] = field(default_factory=list)  # names of rules that fired

    @property
    def is_empty(self) -> bool:
        return not self.key


# ---------------------------------------------------------------------------
# The rules, in order
# ---------------------------------------------------------------------------

def rule_uppercase_and_collapse(text: str) -> str:
    """1. Uppercase, trim, collapse internal whitespace."""
    return re.sub(r"\s+", " ", text).strip().upper()


def rule_strip_quantity_artefacts(text: str) -> str:
    """2. Remove trailing quantity noise: 'QTY 500', 'X100', '(250)'."""
    previous = None
    # Loop because a line can carry more than one artefact: "MPN x100 (250)".
    while previous != text:
        previous = text
        text = QUANTITY_ARTEFACT_RE.sub("", text).strip(" ,;")
    return text


def rule_strip_packaging_suffix(text: str) -> str:
    """3. Remove packaging/reel suffixes.

    Applied repeatedly: real inputs stack them, e.g. '...-TR-ND' carries a reel
    marker and a Digi-Key suffix at once.
    """
    changed = True
    while changed:
        changed = False
        for suffix in PACKAGING_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix) + 3:
                text = text[: -len(suffix)]
                changed = True
                break
    return text


def rule_strip_distributor_prefix(text: str) -> str:
    """4. Remove a leading distributor order-code stub such as '296-'."""
    return DISTRIBUTOR_PREFIX_RE.sub("", text)


def rule_normalize_separators(text: str) -> str:
    """5. Remove separators to form the comparison key.

    'BZX84-C5V6' and 'BZX84C5V6' are the same part written two ways, so the key
    ignores separators entirely. The original is kept for display.
    """
    return SEPARATORS_RE.sub("", text)


def rule_split_grade_suffix(key: str) -> tuple[str, str]:
    """6. Split a trailing temperature/package grade off the key.

    Returns (base, grade). Conservative on purpose: it only fires when what
    remains is still long enough to identify a part, because over-trimming turns
    a precise input into an ambiguous prefix.
    """
    if len(key) < 8:
        return key, ""
    match = GRADE_SUFFIX_RE.match(key)
    if not match:
        return key, ""
    base, grade = match.group("base"), match.group("grade")
    if not grade or len(base) < 6:
        return key, ""
    return base, grade


def confusable_variants(key: str, limit: int = 8) -> list[str]:
    """Alternative readings of O/0 and I/1 confusion.

    Non-destructive by design: rather than picking one reading, generate the
    plausible ones and let the matcher decide by looking them up. Capped,
    because a key with many confusable characters would otherwise produce an
    exponential number of candidates for no benefit.
    """
    positions = [i for i, ch in enumerate(key) if ch in CONFUSABLE]
    if not positions or len(positions) > 6:
        return []

    variants = {key}
    for index in positions:
        for existing in list(variants):
            swapped = existing[:index] + CONFUSABLE[existing[index]] + existing[index + 1:]
            variants.add(swapped)
            if len(variants) > limit * 4:
                break

    variants.discard(key)
    return sorted(variants)[:limit]


# The pipeline as data, so tests can walk it rule by rule and the UI can show
# which rules fired on a given input.
TEXT_RULES = [
    ("uppercase_and_collapse", rule_uppercase_and_collapse),
    ("strip_quantity_artefacts", rule_strip_quantity_artefacts),
    ("strip_packaging_suffix", rule_strip_packaging_suffix),
    ("strip_distributor_prefix", rule_strip_distributor_prefix),
]


def normalize(text: str) -> NormalizedInput:
    """Run the full pipeline over one input string."""
    original = text
    applied: list[str] = []

    current = text
    for name, rule in TEXT_RULES:
        updated = rule(current)
        if updated != current:
            applied.append(name)
        current = updated

    display = current
    key = rule_normalize_separators(current)
    if key != current:
        applied.append("normalize_separators")

    base_key, grade = rule_split_grade_suffix(key)
    if grade:
        applied.append("split_grade_suffix")

    variants = confusable_variants(key)
    if variants:
        applied.append("confusable_variants")

    return NormalizedInput(
        original=original,
        display=display,
        key=key,
        base_key=base_key,
        variants=variants,
        grade=grade,
        applied=applied,
    )
