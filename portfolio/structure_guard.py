from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _round(value: float) -> float:
    return round(value, 6)


def enforce_structure_constraints(
    allocation: dict[str, Any],
    target_position: float,
    *,
    redistribute_unallocated: bool = True,
) -> dict[str, Any]:
    core = _safe_float(allocation.get("core"))
    mainline = _safe_float(allocation.get("mainline"))
    thematic = _safe_float(allocation.get("thematic"))
    target = _safe_float(target_position)
    unallocated = allocation.get("unallocated") or {}
    unallocated_total = sum(_safe_float(value) for value in unallocated.values())
    valid_universe_size = int(_safe_float(allocation.get("valid_universe_size")))

    safe_mode = False
    defensive_absorbed = 0.0
    redistributed = {"mainline": 0.0, "thematic": 0.0}
    if unallocated_total > 0.0:
        if not redistribute_unallocated:
            defensive_absorbed = unallocated_total
        else:
            recipients = {
                "mainline": mainline,
                "thematic": thematic,
            }
            recipient_total = sum(value for value in recipients.values() if value > 0.0)
            if valid_universe_size <= 0 or recipient_total <= 0.0:
                safe_mode = True
            else:
                for sleeve, current in recipients.items():
                    if current <= 0.0:
                        continue
                    addition = unallocated_total * current / recipient_total
                    redistributed[sleeve] = addition
                    if sleeve == "mainline":
                        mainline += addition
                    else:
                        thematic += addition

    active = core + mainline + thematic
    defensive = max(0.0, 100.0 - active)
    portfolio_total = core + mainline + thematic + defensive
    active_sum_ok = (
        active <= target + 1e-6
        if safe_mode or not redistribute_unallocated
        else abs(active - target) <= 1e-6
    )
    portfolio_sum_ok = abs(portfolio_total - 100.0) <= 1e-6
    violation = not active_sum_ok or not portfolio_sum_ok

    return {
        "allocation": {
            "core": _round(core),
            "mainline": _round(mainline),
            "thematic": _round(thematic),
            "defensive": _round(defensive),
        },
        "structure_guard_report": {
            "target_position_ratio": _round(target),
            "active_position_ratio": _round(active),
            "portfolio_total_ratio": _round(portfolio_total),
            "total_sum_check": portfolio_sum_ok,
            "active_sum_check": active_sum_ok,
            "violation": violation,
            "safe_mode_triggered": safe_mode,
            "valid_universe_size": valid_universe_size,
            "unallocated_ratio": _round(unallocated_total),
            "unallocated_policy": (
                "redistribute" if redistribute_unallocated else "defensive_absorb"
            ),
            "defensive_absorbed_ratio": _round(defensive_absorbed),
            "redistributed_ratio": {
                key: _round(value) for key, value in redistributed.items()
            },
        },
    }
