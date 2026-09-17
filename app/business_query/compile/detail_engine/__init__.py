"""Bundle-driven typed detail resolution and bounded reads."""

from app.business_query.compile.detail_engine.engine import (
    DetailEngine,
    align_detail_selections_to_explicit_question,
    expand_all_visible_detail_selections,
)
from app.business_query.compile.detail_engine.projection import (
    _LOCAL_DETAIL_SOURCE_COLUMNS,
    LOCAL_DETAIL_SOURCE_COLUMNS,
    CanonicalDetailSelection,
    DetailReadResult,
    DetailSelectionRefused,
)

__all__ = [
    "CanonicalDetailSelection",
    "DetailEngine",
    "DetailReadResult",
    "DetailSelectionRefused",
    "LOCAL_DETAIL_SOURCE_COLUMNS",
    "_LOCAL_DETAIL_SOURCE_COLUMNS",
    "align_detail_selections_to_explicit_question",
    "expand_all_visible_detail_selections",
]
