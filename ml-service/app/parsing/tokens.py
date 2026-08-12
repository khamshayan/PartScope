"""How part-number-like is a string?

This is the primitive both parsers stand on. The email parser uses it to pick
the part number out of a line of prose; the spreadsheet parser uses it to work
out which column holds part numbers without trusting the header text.

There is no clean rule for "is this an MPN" -- the space includes STM32F103C8T6,
CRCW060310K0FKEA, 1N4148, D38999/24JA103SN and JANTX2N2222A. So this scores a
handful of independent signals and adds them up, and every signal is named so a
wrong answer can be traced to the rule that caused it rather than to a number
that fell out of a model.
"""

from __future__ import annotations

import re

# Length bounds. The brief says 5-25; the wider bounds here admit 1N4148 (6) and
# short passives while still rejecting prose words and pasted paragraphs.
MIN_LENGTH = 4
MAX_LENGTH = 32
IDEAL_MIN = 5
IDEAL_MAX = 25

ACCEPT_THRESHOLD = 0.5

# Things that are definitively not part numbers, however alphanumeric.
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_URL = re.compile(r"^(https?://|www\.)", re.IGNORECASE)
_MONEY = re.compile(r"^[$€£]\s*[\d.,]+$")
_DATE = re.compile(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}$")
_TIME = re.compile(r"^\d{1,2}:\d{2}")
_PURE_NUMBER = re.compile(r"^[\d.,]+$")
_PHONE = re.compile(r"^\+?\d[\d\s().-]{7,}$")

# Words that turn up in RFQ prose and happen to carry a digit.
_PROSE = {
    "COVID19", "Q1", "Q2", "Q3", "Q4", "24H", "48H", "72H", "1ST", "2ND", "3RD",
    "RFQ", "PO", "ASAP", "EAU", "MOQ", "NCNR", "ROHS", "REACH", "AS6171",
}

# Units and quantity words -- these sit next to part numbers and must not be
# mistaken for them.
_UNIT_LIKE = re.compile(
    r"^\d+[\d,.]*\s*(pcs?|pieces?|ea|units?|k|m|reels?|tubes?|trays?)$",
    re.IGNORECASE,
)

_ALLOWED_PUNCT = set("-_/.+#,")


def strip_edges(token: str) -> str:
    """Trim punctuation a sentence attached, keeping punctuation a part uses.

    Trailing commas and full stops are grammar; a leading '(' is grammar. But
    BZX84-C10,215 really does contain a comma and MCP645-I/SN really does
    contain a slash, so the trim is one-sided and conservative.
    """
    token = token.strip().strip('"\'()[]{}<>')
    while token and token[-1] in ".,;:!?":
        token = token[:-1]
    while token and token[0] in ".,;:!?":
        token = token[1:]
    return token


def score_token(token: str) -> float:
    """0..1 estimate that `token` is a manufacturer part number."""
    token = strip_edges(token)
    if not token:
        return 0.0

    upper = token.upper()

    # Hard rejects. These are cheap and they carry no ambiguity.
    if len(token) < MIN_LENGTH or len(token) > MAX_LENGTH:
        return 0.0
    if _EMAIL.search(token) or _URL.match(token):
        return 0.0
    if _MONEY.match(token) or _DATE.match(token) or _TIME.match(token):
        return 0.0
    if _PURE_NUMBER.match(token) or _PHONE.match(token):
        return 0.0
    if _UNIT_LIKE.match(token):
        return 0.0
    if upper in _PROSE:
        return 0.0

    has_alpha = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    # The one near-universal property of an MPN: letters AND digits.
    if not (has_alpha and has_digit):
        return 0.0

    # Anything left is plausible; the rest is a weighted opinion about how
    # plausible. Signals are additive and capped, so no single one dominates.
    score = 0.45

    if IDEAL_MIN <= len(token) <= IDEAL_MAX:
        score += 0.15
    else:
        score -= 0.10

    # Real MPNs are overwhelmingly written upper case.
    letters = [c for c in token if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        score += 0.12
    elif letters and all(c.islower() for c in letters):
        # A lowercase paste is common and legitimate -- no bonus, no penalty.
        score += 0.02

    # A digit somewhere after the first character: MPNs are almost always
    # letters-then-digits, where an English word with a digit rarely is.
    if re.search(r"[A-Za-z]\d", token):
        score += 0.15

    # Alternation between letter and digit runs -- STM32F103C8T6 has many,
    # "Warehouse2" has one.
    runs = len(re.findall(r"(?:[A-Za-z]+\d|\d+[A-Za-z])", token))
    score += min(runs, 3) * 0.05

    # Separators MPNs actually use.
    if any(c in _ALLOWED_PUNCT for c in token):
        score += 0.05

    digit_ratio = sum(c.isdigit() for c in token) / len(token)
    # Mostly-digits with a stray letter is usually a quantity or a code;
    # mostly-letters with a stray digit is usually a word.
    if 0.15 <= digit_ratio <= 0.75:
        score += 0.10
    else:
        score -= 0.05

    # Characters that never appear in a part number.
    if re.search(r"[^\w\-/.+#,]", token):
        score -= 0.25

    return max(0.0, min(1.0, score))


def looks_like_part_number(token: str) -> bool:
    return score_token(token) >= ACCEPT_THRESHOLD


def best_candidate(text: str) -> tuple[str, float]:
    """Highest-scoring part-number-like token in a string.

    Returns ("", 0.0) when nothing in the text clears the bar, which is how a
    caller distinguishes "this line is prose" from "this line is a part".
    """
    best, best_score = "", 0.0
    for raw in re.split(r"[\s\t]+", text):
        token = strip_edges(raw)
        if not token:
            continue
        current = score_token(token)
        if current > best_score:
            best, best_score = token, current
    return (best, best_score) if best_score >= ACCEPT_THRESHOLD else ("", best_score)


def looks_like_quantity(value: str) -> bool:
    """A bare count: 500, 1,000, 2 500. Not a price, not a part."""
    cleaned = value.strip().replace(",", "").replace(" ", "")
    return cleaned.isdigit() and 0 < len(cleaned) <= 9


def parse_quantity(value: str) -> int | None:
    cleaned = re.sub(r"[,\s]", "", str(value).strip())
    if not cleaned.isdigit():
        return None
    number = int(cleaned)
    return number if number > 0 else None


def looks_like_currency(value: str) -> bool:
    text = str(value).strip()
    if _MONEY.match(text):
        return True
    # 2.50 / 0.0431 -- a bare decimal in a BOM is nearly always a price.
    return bool(re.match(r"^\d+\.\d{1,6}$", text))
