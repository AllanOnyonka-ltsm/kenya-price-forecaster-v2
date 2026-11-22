"""Pure helpers that turn raw RandomForest feature importances into the
human-readable factor groups shown by the /explainability endpoint.

No third-party imports, so it can be unit-tested without loading the model.
"""

from typing import Dict, List, Sequence, Tuple

# Ordered (factor name, feature columns belonging to it).
FACTOR_GROUPS: List[Tuple[str, Tuple[str, ...]]] = [
    ("3-Month Price Average", ("price_ma_3",)),
    ("Previous Month Price",  ("price_lag_1",)),
    ("Price Volatility",      ("price_vol_6",)),
    ("Seasonality",           ("sin_month", "cos_month", "month", "quarter")),
    ("Market Location",       ("market", "latitude", "longitude", "admin1")),
]
OTHER_FACTOR = "Other"


def impact_label(importance: float) -> str:
    if importance >= 0.5:
        return "Very High"
    if importance >= 0.2:
        return "High"
    if importance >= 0.05:
        return "Medium"
    if importance >= 0.01:
        return "Low"
    return "Minimal"


def group_feature_importances(
    importances: Sequence[float], feature_columns: Sequence[str], ndigits: int = 4
) -> List[Tuple[str, float]]:
    """Sum importances per factor group; return (factor, importance) sorted descending.

    Columns not in any group go to "Other", which is included only if nonzero.
    """
    if len(importances) != len(feature_columns):
        raise ValueError(
            f"importances ({len(importances)}) and feature_columns "
            f"({len(feature_columns)}) differ in length"
        )
    col_to_factor: Dict[str, str] = {
        col: factor for factor, cols in FACTOR_GROUPS for col in cols
    }
    totals: Dict[str, float] = {factor: 0.0 for factor, _ in FACTOR_GROUPS}
    other = 0.0
    for col, imp in zip(feature_columns, importances):
        factor = col_to_factor.get(col)
        if factor is None:
            other += float(imp)
        else:
            totals[factor] += float(imp)
    if other > 0:
        totals[OTHER_FACTOR] = other
    grouped = [(f, round(v, ndigits)) for f, v in totals.items()]
    grouped.sort(key=lambda fv: fv[1], reverse=True)
    return grouped
