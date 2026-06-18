from __future__ import annotations

from shadow_app.allocator import (
    compare_actual_to_target,
    extract_etf_candidates,
    risk_budget_from_market,
    target_allocations,
)


def test_extract_etf_candidates_from_mainline_text() -> None:
    text = "588170.SH 华夏半导体ETF、159516.SZ 国泰半导体ETF、588710.SH 华泰半导体ETF"

    result = extract_etf_candidates(text)

    assert [row["code"] for row in result] == ["588170.SH", "159516.SZ", "588710.SH"]
    assert result[0]["name"] == "华夏半导体ETF"


def test_risk_budget_uses_market_range_midpoint() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "equity_position_range": "35%-45%",
                    "market_position_score": 46.98,
                }
            }
        }
    }

    assert risk_budget_from_market(market_payload) == 40.0


def test_target_allocations_stay_inside_risk_budget() -> None:
    market_payload = {
        "results": {"market_score": {"record": {"equity_position_range": "35%-45%"}}}
    }
    theme_payload = {
        "theme_signals": [
            {
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 60,
                "evidence_score": 90,
                "top_etf": "588170.SH 华夏半导体ETF、159516.SZ 国泰半导体ETF",
            },
            {
                "theme": "创新药/医药",
                "stage": "弱势/退潮",
                "score_weight_ratio": 40,
                "evidence_score": 20,
                "top_etf": "512010.SH 医药ETF",
            },
        ]
    }

    budget, rows = target_allocations(market_payload, theme_payload, {})

    assert budget == 40.0
    assert round(sum(row["target_weight_ratio"] for row in rows), 6) == 40.0
    assert [row["code"] for row in rows] == ["588170.SH", "159516.SZ"]


def test_compare_actual_to_target_is_ratio_only() -> None:
    actual = [{"code": "588170.SH", "name": "A", "weight_ratio": 8.0}]
    target = [{"code": "588170.SH", "name": "A", "target_weight_ratio": 10.0, "theme": "T"}]

    rows = compare_actual_to_target(actual, target)

    assert rows[0]["difference_ratio"] == -2.0
    assert rows[0]["status"] == "低于影子目标"
