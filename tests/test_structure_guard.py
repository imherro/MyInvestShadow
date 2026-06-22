from __future__ import annotations

from portfolio.structure_guard import enforce_structure_constraints


def test_structure_guard_redistributes_unallocated_to_valid_non_core_pool() -> None:
    result = enforce_structure_constraints(
        {
            "core": 15.0,
            "mainline": 12.501,
            "thematic": 0.0,
            "unallocated": {"thematic": 2.499},
            "valid_universe_size": 1,
        },
        30.0,
    )

    assert result["allocation"] == {
        "core": 15.0,
        "mainline": 15.0,
        "thematic": 0.0,
        "defensive": 70.0,
    }
    assert result["structure_guard_report"]["safe_mode_triggered"] is False
    assert result["structure_guard_report"]["total_sum_check"] is True
    assert result["structure_guard_report"]["active_sum_check"] is True


def test_structure_guard_safe_mode_reduces_exposure_when_universe_empty() -> None:
    result = enforce_structure_constraints(
        {
            "core": 15.0,
            "mainline": 0.0,
            "thematic": 0.0,
            "unallocated": {"mainline": 12.501, "thematic": 2.499},
            "valid_universe_size": 0,
        },
        30.0,
    )

    assert result["allocation"] == {
        "core": 15.0,
        "mainline": 0.0,
        "thematic": 0.0,
        "defensive": 85.0,
    }
    assert result["structure_guard_report"]["safe_mode_triggered"] is True
    assert result["structure_guard_report"]["active_sum_check"] is True
    assert result["structure_guard_report"]["total_sum_check"] is True
    assert result["structure_guard_report"]["violation"] is False
