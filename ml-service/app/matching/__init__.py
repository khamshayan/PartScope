"""Part-number matching: normalization, catalog index, cascade, alternates."""

from .alternates import AlternateFinder
from .catalog import CatalogIndex, CatalogPart, load_from_mongo
from .matcher import MatchResult, PartMatcher, get_matcher
from .normalize import NormalizedInput, normalize

__all__ = [
    "AlternateFinder",
    "CatalogIndex",
    "CatalogPart",
    "load_from_mongo",
    "MatchResult",
    "PartMatcher",
    "get_matcher",
    "NormalizedInput",
    "normalize",
]
