from __future__ import annotations

from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _base_position(market_score: float) -> float:
    if market_score >= 70.0:
        return 0.60
    if market_score >= 55.0:
        return 0.45
    if market_score >= 40.0:
        return 0.30
    return 0.15


def _confidence_adjustment(confidence: float) -> float:
    confidence_value = _clamp(float(confidence), 0.0, 1.0)
    return 0.85 + 0.15 * confidence_value


def _regime_adjustment(regime: str | None) -> float:
    if regime is None:
        return 0.0
    value = regime.strip().lower()
    if value == "risk_on":
        return 0.10
    if value == "risk_off":
        return -0.10
    return 0.0


def compute_target_position(
    market_score: float,
    confidence: float,
    regime: str | None = None,
) -> dict[str, Any]:
    base = _base_position(float(market_score))
    confidence_adj = _confidence_adjustment(confidence)
    regime_adj = _regime_adjustment(regime)
    raw_position = base * confidence_adj * (1.0 + regime_adj)
    final_position = _clamp(raw_position, 0.05, 0.80)
    return {
        "final_position": round(final_position, 6),
        "components": {
            "base": base,
            "confidence_adj": round(confidence_adj, 6),
            "regime_adj": regime_adj,
        },
    }
