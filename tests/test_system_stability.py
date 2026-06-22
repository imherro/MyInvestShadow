from __future__ import annotations

import json

from shadow_app.allocator import allocation_plan, sleeve_summary
from shadow_app.pricing import PricePoint


def _market(score: float, confidence: float = 1.0, regime: str = "neutral") -> dict:
    return {
        "results": {
            "market_score": {
                "record": {
                    "market_position_score": score,
                    "confidence": confidence,
                    "market_regime": regime,
                }
            }
        }
    }


def _signal(rank: int, theme: str, code: str, score: float = 90.0) -> dict:
    return {
        "rank": rank,
        "theme": theme,
        "stage": "主线确认",
        "score_weight_ratio": score,
        "evidence_score": score,
        "top_etf": f"{code} {theme}ETF",
    }


def _theme(signals: list[dict]) -> dict:
    return {"theme_signals": signals}


def _point(code: str, *, amount: float = 500_000, r5: float = 5.0, r20: float = 10.0) -> PricePoint:
    return PricePoint(
        code=code,
        close=1.0,
        pct_chg=1.0,
        source="test",
        amount=amount,
        r5=r5,
        r20=r20,
        amount_rank=0.9 if amount >= 300_000 else 0.1,
        premium_rate=0.1,
    )


def _stable_theme_and_prices() -> tuple[dict, dict[str, PricePoint]]:
    signals = [
        _signal(1, "硬科技电子/半导体", "588170.SH"),
        _signal(2, "AI算力/通信", "515050.SH", 85.0),
    ]
    prices = {
        "588170.SH": _point("588170.SH"),
        "515050.SH": _point("515050.SH"),
    }
    return _theme(signals), prices


def test_cross_market_regime_stability() -> None:
    theme_payload, price_map = _stable_theme_and_prices()
    cases = [
        _market(75.0, 1.0, "risk_on"),
        _market(50.0, 0.70, "neutral"),
        _market(25.0, 0.35, "risk_off"),
        _market(45.0, 1.0, "neutral"),
    ]

    for market_payload in cases:
        plan = allocation_plan(market_payload, theme_payload, price_map)
        summary = sleeve_summary(plan["targets"])
        guard = plan["structure_guard_report"]

        assert guard["violation"] is False
        assert guard["total_sum_check"] is True
        assert round(sum(summary.values()), 6) == 100.0
        assert summary["core"] <= plan["market_risk_budget_ratio"]
        assert summary["defensive"] >= 0.0


def test_etf_universe_collapse_does_not_create_core_absorption() -> None:
    signals = [
        _signal(1, "硬科技电子/半导体", "588170.SH"),
        _signal(2, "AI算力/通信", "515050.SH", 85.0),
        _signal(3, "机器人", "562500.SH", 80.0),
    ]
    price_map = {
        "562500.SH": _point("562500.SH"),
    }

    plan = allocation_plan(_market(46.98), _theme(signals), price_map)
    summary = sleeve_summary(plan["targets"])
    guard = plan["structure_guard_report"]

    assert plan["gate_universe_audit"]["pre_gate_universe_size"] >= 3
    assert plan["gate_universe_audit"]["post_gate_universe_size"] == 1
    assert summary["core"] == 15.0
    assert round(plan["risk_budget_ratio"], 4) == 30.0
    assert guard["safe_mode_triggered"] is False
    assert guard["violation"] is False
    assert guard["redistributed_ratio"]["mainline"] > 0.0

    full_collapse = allocation_plan(_market(46.98), _theme(signals), {})
    full_summary = sleeve_summary(full_collapse["targets"])
    full_guard = full_collapse["structure_guard_report"]

    assert full_collapse["gate_universe_audit"]["post_gate_universe_size"] == 0
    assert full_summary["core"] == 15.0
    assert full_summary["defensive"] == 85.0
    assert round(full_collapse["risk_budget_ratio"], 4) == 15.0
    assert full_guard["safe_mode_triggered"] is True
    assert full_guard["violation"] is False


def test_signal_noise_injection_keeps_position_variance_bounded() -> None:
    theme_payload, price_map = _stable_theme_and_prices()
    active_positions = []
    for score in [42.28, 44.63, 46.98, 49.33, 51.68]:
        plan = allocation_plan(_market(score), theme_payload, price_map)
        active_positions.append(plan["risk_budget_ratio"])
        assert plan["structure_guard_report"]["violation"] is False

    max_position_variance = max(active_positions) - min(active_positions)

    assert max_position_variance == 0.0


def test_deterministic_stress_100_runs() -> None:
    theme_payload, price_map = _stable_theme_and_prices()
    market_payload = _market(46.98)
    outputs = [
        json.dumps(
            allocation_plan(market_payload, theme_payload, price_map),
            ensure_ascii=False,
            sort_keys=True,
        )
        for _ in range(100)
    ]

    assert len(set(outputs)) == 1
