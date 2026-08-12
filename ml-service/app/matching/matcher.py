"""The matching cascade: messy string in, ranked candidates out.

Five tiers, tried in order, each with a confidence ceiling that reflects how
much evidence it actually has:

  1.00  exact       the normalized key is in the catalog
  0.95  variant     an O/0 or I/1 reading of the key is in the catalog
  0.85  prefix      the input is a truncation of one or more catalog parts
  0.85  base        the input matches a part once its grade suffix is ignored
  0.50-0.80 fuzzy   rapidfuzz similarity above threshold, scaled
  --    no_match    nothing cleared the bar; near misses returned anyway

The confidences are not calibrated probabilities and are not presented as such.
They are an ordering with a documented meaning per tier, which is what a buyer
deciding whether to eyeball a row actually needs. The 0.7 review threshold in
the UI sits deliberately between the prefix tier and the top of the fuzzy tier:
anything a human should re-read is fuzzy-matched.

Why no_match still returns near misses: a buyer who typed a part that is not in
the catalog is better served by three wrong-but-close options they can reject in
two seconds than by an empty row telling them nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .alternates import AlternateFinder
from .catalog import CatalogIndex, CatalogPart
from .normalize import NormalizedInput, normalize

# rapidfuzz similarity below this is not evidence of anything. 82 was chosen by
# running the evaluation set: below it the false-positive rate on deliberately
# non-existent parts climbs sharply without recovering real matches.
FUZZY_THRESHOLD = 82.0

# Confidence band the fuzzy tier maps onto. Capped below the prefix tier so an
# exact-but-truncated input always outranks a guess.
FUZZY_CONFIDENCE_MIN = 0.50
FUZZY_CONFIDENCE_MAX = 0.80

MAX_CANDIDATES = 10
NEAR_MISS_COUNT = 3


@dataclass
class Candidate:
    part: CatalogPart
    confidence: float
    method: str

    def to_dict(self) -> dict:
        payload = self.part.to_dict()
        payload["confidence"] = round(self.confidence, 3)
        payload["match_method"] = self.method
        return payload


@dataclass
class MatchResult:
    input_string: str
    normalized: NormalizedInput
    candidates: list[Candidate]
    near_misses: list[Candidate] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.candidates)

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def method(self) -> str:
        return self.best.method if self.best else "no_match"

    @property
    def confidence(self) -> float:
        return self.best.confidence if self.best else 0.0


def _scale_fuzzy(score: float) -> float:
    """Map a rapidfuzz score in [threshold, 100] onto the fuzzy band."""
    span = 100.0 - FUZZY_THRESHOLD
    fraction = (score - FUZZY_THRESHOLD) / span if span else 0.0
    fraction = max(0.0, min(1.0, fraction))
    return FUZZY_CONFIDENCE_MIN + fraction * (FUZZY_CONFIDENCE_MAX - FUZZY_CONFIDENCE_MIN)


def _fuzzy_score(query: str, choice: str, **kwargs) -> float:
    """Max of token-set and partial ratio.

    They fail in different directions -- token_set handles reordering and extra
    tokens, partial handles one string being contained in the other -- so taking
    the better of the two catches both shapes of mess.

    **kwargs absorbs the `score_cutoff` and `processor` that rapidfuzz passes to
    scorers it drives itself; the cutoff is applied by the caller instead.
    """
    return max(fuzz.token_set_ratio(query, choice), fuzz.partial_ratio(query, choice))


class PartMatcher:
    def __init__(self, index: CatalogIndex):
        self.index = index
        self.alternates = AlternateFinder(index)

    # -- the cascade -------------------------------------------------------

    def match(self, text: str) -> MatchResult:
        norm = normalize(text)
        if norm.is_empty:
            return MatchResult(text, norm, [])

        # Tier 1: exact on the normalized key.
        exact = self.index.exact(norm.key)
        if exact:
            return MatchResult(text, norm, self._rank(exact, 1.0, "exact", text))

        # Tier 2: an O/0 or I/1 reading of the key.
        for variant in norm.variants:
            hits = self.index.exact(variant)
            if hits:
                return MatchResult(text, norm,
                                   self._rank(hits, 0.95, "confusable_variant", text))

        # Tier 3: the input is a truncation of one or more catalog parts.
        # Requires a reasonably long stem; a three-character prefix matches
        # half the catalog and tells the buyer nothing.
        if len(norm.key) >= 6:
            prefixed = self.index.prefix(norm.key, limit=MAX_CANDIDATES)
            if prefixed:
                return MatchResult(text, norm, self._rank(prefixed, 0.85, "prefix", text))

        # Tier 4: the catalog part carries a grade suffix the input omitted, or
        # vice versa. Same idea as tier 3 from the other direction.
        base_hits = self.index.by_base(norm.key) or (
            self.index.exact(norm.base_key) if norm.base_key != norm.key else []
        )
        if base_hits:
            return MatchResult(text, norm, self._rank(base_hits, 0.85, "base_part", text))

        # Tier 5: fuzzy.
        scored = process.extract(
            norm.key,
            self.index.fuzz_keys,
            scorer=_fuzzy_score,
            limit=MAX_CANDIDATES,
        )
        above = [(key, score) for key, score, _ in scored if score >= FUZZY_THRESHOLD]
        if above:
            candidates: list[Candidate] = []
            for key, score in above:
                for part in self.index.exact(key):
                    candidates.append(Candidate(part, _scale_fuzzy(score), "fuzzy"))
            if candidates:
                candidates.sort(key=lambda c: (-c.confidence, c.part.is_obsolete, c.part.mpn))
                return MatchResult(text, norm, candidates[:MAX_CANDIDATES])

        # Nothing cleared the bar. Return the closest things anyway so a human
        # can eyeball them -- an empty row is less useful than a rejected guess.
        near = []
        for key, score, _ in scored[:NEAR_MISS_COUNT]:
            for part in self.index.exact(key):
                near.append(Candidate(part, round(score / 100.0, 3), "near_miss"))
                break
        return MatchResult(text, norm, [], near_misses=near[:NEAR_MISS_COUNT])

    def _rank(self, parts: list[CatalogPart], confidence: float,
              method: str, raw_input: str = "") -> list[Candidate]:
        """Order equally-confident hits so the most useful one leads.

        Two tie-breaks, in order:

        1. A part whose printed MPN is literally what the user typed wins. Real
           catalogs contain siblings that differ only in separators -- both
           'BZX84-C10,215' and 'BZX84C10,215' exist -- and they collapse to the
           same normalized key. If the buyer typed one of them exactly, that is
           not a coincidence to be broken alphabetically.
        2. Otherwise prefer parts you can actually buy: active before obsolete,
           stocked before not.
        """
        literal = raw_input.strip().upper()
        ordered = sorted(
            parts,
            key=lambda p: (
                p.mpn.upper() != literal,
                p.is_obsolete,
                p.authorized_stock == 0,
                p.mpn,
            ),
        )
        return [Candidate(p, confidence, method) for p in ordered[:MAX_CANDIDATES]]

    # -- presentation ------------------------------------------------------

    def analyze(self, text: str, with_alternates: bool = True) -> dict:
        """One line item, in the shape the API and the UI consume."""
        result = self.match(text)
        best = result.best

        payload = {
            "input_string": text,
            "normalized_key": result.normalized.key,
            "rules_applied": result.normalized.applied,
            "matched_mpn": best.part.mpn if best else None,
            "manufacturer": best.part.manufacturer if best else None,
            "description": best.part.description if best else None,
            "category": best.part.category if best else None,
            "lifecycle_status": best.part.lifecycle_status if best else None,
            "authorized_stock": best.part.authorized_stock if best else None,
            "is_defense_grade": best.part.is_defense_grade if best else None,
            "date_introduced": best.part.date_introduced if best else None,
            "confidence": round(result.confidence, 3),
            "match_method": result.method,
            "candidates": [c.to_dict() for c in result.candidates[:NEAR_MISS_COUNT]],
            "near_misses": [c.to_dict() for c in result.near_misses],
            "alternates": [],
        }
        if best and with_alternates:
            payload["alternates"] = self.alternates.find(best.part)
        return payload


_MATCHER: PartMatcher | None = None


def get_matcher() -> PartMatcher:
    """Process-wide matcher over a lazily built index."""
    global _MATCHER
    if _MATCHER is None:
        from .catalog import load_from_mongo

        _MATCHER = PartMatcher(load_from_mongo())
    return _MATCHER
