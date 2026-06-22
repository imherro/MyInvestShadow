from __future__ import annotations

from portfolio.position_sizer import compute_target_position


def test_score_boundaries_select_base_position() -> None:
    assert compute_target_position(70, 1.0)["components"]["base"] == 0.60
    assert compute_target_position(55, 1.0)["components"]["base"] == 0.45
    assert compute_target_position(40, 1.0)["components"]["base"] == 0.30
    assert compute_target_position(39.99, 1.0)["components"]["base"] == 0.15


def test_confidence_adjustment_bounds() -> None:
    low = compute_target_position(55, 0.0)
    high = compute_target_position(55, 1.0)

    assert low["components"]["confidence_adj"] == 0.85
    assert high["components"]["confidence_adj"] == 1.0
    assert low["final_position"] == 0.3825
    assert high["final_position"] == 0.45


def test_regime_adjustment() -> None:
    risk_on = compute_target_position(55, 1.0, "risk_on")
    neutral = compute_target_position(55, 1.0, "neutral")
    risk_off = compute_target_position(55, 1.0, "risk_off")

    assert risk_on["components"]["regime_adj"] == 0.10
    assert neutral["components"]["regime_adj"] == 0.0
    assert risk_off["components"]["regime_adj"] == -0.10
    assert risk_on["final_position"] == 0.495
    assert neutral["final_position"] == 0.45
    assert risk_off["final_position"] == 0.405


def test_output_is_clamped_and_deterministic() -> None:
    first = compute_target_position(1000, 99.0, "risk_on")
    second = compute_target_position(1000, 99.0, "risk_on")
    third = compute_target_position(1000, 99.0, "risk_on")

    assert first == second == third
    assert 0.05 <= first["final_position"] <= 0.80
    assert first["final_position"] == 0.66
