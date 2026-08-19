"""
Metadata normalization for exact-match SOP retrieval.

Handles:
  - case-insensitivity, '&' <-> 'and', separator cleanup
  - Chroma-safe metadata fields (strings only)

Note: Audit parameters match SOP subcategories word-for-word (same taxonomy),
so exact matching after normalization is sufficient. No stemming/fuzzy needed.
"""

import re

_SEP = re.compile(r"[-/>|:]")


# ---------------------------------------------------------
# Normalization
# ---------------------------------------------------------
def normalize_metadata_value(value: str) -> str:
    """Case-insensitive; '&'->'and'; separators & extra spaces cleaned."""
    value = (value or "").strip().casefold()
    value = value.replace("&", " and ")
    value = _SEP.sub(" ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


# ---------------------------------------------------------
# Chroma-safe metadata builder (str/int/float/bool only)
# ---------------------------------------------------------
def build_search_fields(category: str, subcategory: str) -> dict:
    cat_norm = normalize_metadata_value(category)
    sub_norm = normalize_metadata_value(subcategory)
    combined = f"{cat_norm} {sub_norm}".strip()

    return {
        "category": cat_norm,
        "subcategory": sub_norm,
        "category_combined": combined,   # e.g. "personal touch personalization"
    }