"""The one structure every parser produces.

Email text and spreadsheets arrive in completely different shapes, but both
feed the same matcher, so both funnel into ParsedItem here. Anything a parser
learned along the way that a human might need to audit -- which sheet it read,
which column it decided was the part number, which lines it threw away -- goes
in ParseResult rather than being logged and forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedItem:
    input_string: str
    quantity: int | None = None
    # 0..1, how part-number-like the chosen token was. The UI does not show
    # this; it exists so a low-confidence extraction can be explained.
    token_score: float = 0.0
    source_line: int | None = None

    def to_dict(self) -> dict:
        return {
            "input_string": self.input_string,
            "quantity": self.quantity,
            "token_score": round(self.token_score, 3),
            "source_line": self.source_line,
        }


@dataclass
class ParseResult:
    items: list[ParsedItem] = field(default_factory=list)
    # Lines that were read and deliberately discarded, verbatim. A line that
    # vanished silently between the paste and the results table is the one
    # failure a buyer would never catch.
    skipped: list[str] = field(default_factory=list)
    # Free-form notes about how the parse was done, shown to the user.
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "skipped": self.skipped,
            "diagnostics": self.diagnostics,
            "count": len(self.items),
        }
