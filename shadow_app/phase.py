from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_shadow_phase(signal: dict[str, Any]) -> dict[str, str]:
    cycle_stage = str(signal.get("cycle_stage") or "").lower()
    lifecycle = str(signal.get("lifecycle_state") or "").lower()
    market_score = _safe_float(signal.get("cycle_market_score") or signal.get("market_score"))
    crowding = str(signal.get("crowding_signal") or "")

    if any(token in cycle_stage for token in ("legacy", "cooling", "decline", "residual")):
        return {
            "shadow_phase": "avoid_or_defensive",
            "instrument_preference": "avoid",
            "phase_reason": "主线处于旧线残余或降温阶段，不主动落地。",
        }

    if "crowded" in cycle_stage or "拥挤" in crowding:
        return {
            "shadow_phase": "emotion_game",
            "instrument_preference": "small_or_none",
            "phase_reason": "方向仍有热度但结构偏拥挤，优先小仓或不做。",
        }

    if any(token in cycle_stage for token in ("convergence", "alpha", "leader", "main_rise_late")):
        return {
            "shadow_phase": "alpha_convergence",
            "instrument_preference": "leader",
            "phase_reason": "资金向龙头收敛，具备龙头仓观察条件。",
        }

    if any(token in cycle_stage for token in ("launch", "confirmation", "main_rise", "trend")):
        if market_score is not None and market_score >= 55:
            return {
                "shadow_phase": "beta_to_alpha",
                "instrument_preference": "etf_then_leader",
                "phase_reason": "方向已被市场确认，先用ETF承接，龙头只在上游确认时补充。",
            }
        return {
            "shadow_phase": "beta_dominant",
            "instrument_preference": "etf",
            "phase_reason": "方向确认但市场强度不足，优先ETF而非个股。",
        }

    if "policy_incubation" in cycle_stage or "accelerating" in lifecycle:
        return {
            "shadow_phase": "beta_dominant",
            "instrument_preference": "etf",
            "phase_reason": "政策或事件在升温，但市场确认不足，优先ETF观察。",
        }

    return {
        "shadow_phase": "watch",
        "instrument_preference": "etf",
        "phase_reason": "缺少足够阶段证据，按ETF备选观察。",
    }


def stage_from_cycle(signal: dict[str, Any], fallback_stage: str = "") -> str:
    phase = classify_shadow_phase(signal)
    cycle_label = str(signal.get("cycle_stage_label") or "").strip()
    suffix = f"/{cycle_label}" if cycle_label else ""
    preference = phase["instrument_preference"]
    if preference in {"leader", "etf_then_leader"}:
        return f"主线确认{suffix}"
    if preference == "etf":
        return f"观察线{suffix or '/ETF优先'}"
    if preference == "small_or_none":
        return f"情绪博弈{suffix}"
    if fallback_stage:
        return fallback_stage
    return f"弱势/退潮{suffix}"
