"""Pull part numbers and quantities out of free-form text.

An RFQ arrives as an email body far more often than as a tidy list, so this has
to cope with greetings, signatures, quoted reply chains, numbered lists, pasted
table fragments and prose wrapped around the actual request.

The strategy is per line: decide whether the line is data at all, then extract
the best part-number-like token from it and look for a quantity nearby. Lines
that yield nothing are recorded in `skipped` rather than dropped -- if the
parser is wrong, the user needs to be able to see what it ignored.
"""

from __future__ import annotations

import re

from .models import ParsedItem, ParseResult
from .tokens import (
    best_candidate,
    looks_like_quantity,
    parse_quantity,
    score_token,
    strip_edges,
)

# Mail headers and boilerplate.
_HEADER = re.compile(
    r"^\s*(from|to|cc|bcc|sent|subject|date|reply-to|importance|attachments?)\s*:",
    re.IGNORECASE,
)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|dear|good\s+(morning|afternoon|evening))\b[\s,!.]*$",
    re.IGNORECASE,
)
_SIGN_OFF = re.compile(
    r"^\s*(regards|kind regards|best regards|best|thanks|thank you|cheers|"
    r"sincerely|br|rgds)\b[\s,!.]*$",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"^\s*>")
_QUOTE_INTRO = re.compile(
    r"^\s*on .+ (wrote|schrieb):|^\s*-+\s*original message\s*-+|"
    r"^\s*from:.*sent:",
    re.IGNORECASE,
)
# A hard signature delimiter: everything after it is signature, by convention.
_SIG_DELIMITER = re.compile(r"^\s*(--\s*$|__+\s*$|sent from my )", re.IGNORECASE)
_RULE = re.compile(r"^\s*[-=_*~#]{3,}\s*$")

# A table header pasted into the body.
_HEADER_ROW = re.compile(
    r"^\s*(part\s*(number|no\.?|#)?|mpn|p/?n|item|component|description)\b.{0,60}"
    r"\b(qty|quantity|amount|price|each|uom)\b",
    re.IGNORECASE,
)

# Quantity phrasings, in priority order. Each captures the number.
_QUANTITY_PATTERNS = [
    re.compile(r"\bx\s*(\d[\d,\s]*)\b", re.IGNORECASE),                  # MPN x 500
    re.compile(r"\bqty\.?\s*[:=]?\s*(\d[\d,\s]*)\b", re.IGNORECASE),     # QTY: 500
    re.compile(r"\bquantity\s*[:=]?\s*(\d[\d,\s]*)\b", re.IGNORECASE),
    re.compile(r"\b(\d[\d,]*)\s*(?:pcs?|pieces?|ea\b|units?)", re.IGNORECASE),
    re.compile(r"\bneed\s+(\d[\d,]*)\b", re.IGNORECASE),
]

_LIST_MARKER = re.compile(r"^\s*(?:[-*•·–]|\(?\d{1,3}[.)])\s+")

# Splitting a delimited line. Comma splitting requires a following space so that
# thousands separators (1,000) and part numbers that contain a comma
# (BZX84-C10,215) survive intact -- both were real bugs.
_DELIMITERS = [re.compile(r"\t+"), re.compile(r"\s*;\s*"),
               re.compile(r",\s+"), re.compile(r"\s{2,}")]

MAX_ITEMS = 500


def _split_fields(line: str) -> list[str]:
    for delimiter in _DELIMITERS:
        fields = [f.strip() for f in delimiter.split(line) if f.strip()]
        if len(fields) > 1:
            return fields
    return [line.strip()]


def _is_price_figure(text: str, start: int, end: int) -> bool:
    """Is the number at [start:end] the digits of a price?

    Asks about the number itself, not about its neighbourhood. An earlier
    version rejected any quantity within a few characters of a price, which
    threw away the 40 in "target $2.50 need 40" -- the price was nearby, but
    the number matched was plainly not it.
    """
    before = text[:start].rstrip()
    if before.endswith(("$", "€", "£", "@")):
        return True
    # A captured "2" that is really the start of "2.50".
    tail = text[end:end + 2]
    return len(tail) == 2 and tail[0] == "." and tail[1].isdigit()


def _quantity_from_text(text: str) -> tuple[int | None, str]:
    """Find a quantity phrase and return it with that phrase removed."""
    for pattern in _QUANTITY_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span(1)
            if _is_price_figure(text, start, end):
                continue
            quantity = parse_quantity(match.group(1))
            if quantity is not None:
                return quantity, (text[:match.start()] + " " + text[match.end():])
    return None, text


def _parse_line(line: str) -> ParsedItem | None:
    body = _LIST_MARKER.sub("", line).strip()
    if not body:
        return None

    fields = _split_fields(body)

    # Delimited line (pasted table row, CSV fragment): the best-scoring field is
    # the part, and the first bare integer after it is the quantity.
    if len(fields) > 1:
        scored = [(score_token(strip_edges(f)), index, f) for index, f in enumerate(fields)]
        best_score, best_index, best_field = max(scored, key=lambda row: row[0])
        if best_score >= 0.5:
            quantity = None
            for field in fields[best_index + 1:]:
                if looks_like_quantity(field):
                    quantity = parse_quantity(field)
                    break
                embedded, _ = _quantity_from_text(field)
                if embedded is not None:
                    quantity = embedded
                    break
            if quantity is None:
                quantity, _ = _quantity_from_text(body)
            return ParsedItem(strip_edges(best_field), quantity, best_score)

    # Free text: strip the quantity phrase first so its digits cannot be
    # mistaken for part of a part number, then take the best remaining token.
    quantity, remainder = _quantity_from_text(body)
    token, token_score = best_candidate(remainder)
    if not token:
        # The quantity phrase may have eaten the part number (e.g. "500 pcs" on
        # its own line); fall back to scanning the untouched line.
        token, token_score = best_candidate(body)
    if not token:
        return None
    return ParsedItem(token, quantity, token_score)


def parse_text(raw_text: str, max_items: int = MAX_ITEMS) -> ParseResult:
    result = ParseResult()
    in_signature = False
    quoted_lines = 0

    for number, line in enumerate(str(raw_text).splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        # Everything past a signature delimiter is signature.
        if _SIG_DELIMITER.match(stripped):
            in_signature = True
        if in_signature:
            result.skipped.append(stripped)
            continue

        # A reply chain: the quoted text is a previous message, not this
        # request. Its part numbers were someone else's ask.
        if _QUOTED.match(line) or _QUOTE_INTRO.match(stripped):
            quoted_lines += 1
            result.skipped.append(stripped)
            continue

        if (_HEADER.match(stripped) or _GREETING.match(stripped)
                or _SIGN_OFF.match(stripped) or _RULE.match(stripped)
                or _HEADER_ROW.match(stripped)):
            result.skipped.append(stripped)
            continue

        item = _parse_line(line)
        if item is None:
            # No token in this line looked like a part number. Passing the
            # whole line through instead would put prose in the results table.
            result.skipped.append(stripped)
            continue

        item.source_line = number
        result.items.append(item)
        if len(result.items) >= max_items:
            break

    result.diagnostics = {
        "parser": "email",
        "lines_read": len(str(raw_text).splitlines()),
        "quoted_lines_skipped": quoted_lines,
        "signature_stripped": in_signature,
    }
    return result
