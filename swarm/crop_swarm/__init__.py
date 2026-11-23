"""crop_swarm: a challenger price-forecasting swarm for the WFP Kenya monthly panel.

Challenger only. It never writes price_api/artifacts/champion.json and does not touch pipeline/retrain.py.
"""
from .agents import (MAX_NUDGE, NARRATIVE_SCHEMA, NarrativeAgent, RiskBandAgent, SupplySeasonAgent,
                     TrendSeasonalAgent, bounded_nudge, weighted_median)
from .llm_provider import get_provider
from .series import load_panel, to_dense
from .swarm import Swarm

__all__ = [
    "MAX_NUDGE", "NARRATIVE_SCHEMA", "NarrativeAgent", "RiskBandAgent", "SupplySeasonAgent",
    "TrendSeasonalAgent", "bounded_nudge", "weighted_median", "get_provider", "load_panel",
    "to_dense", "Swarm",
]
