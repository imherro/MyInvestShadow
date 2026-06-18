from __future__ import annotations

from shadow_app.allocator import (
    extract_etf_candidates,
    risk_budget_from_market,
    sleeve_summary,
    target_allocations,
)
from shadow_app.upstream import normalize_theme_payload


def test_extract_etf_candidates_from_mainline_text() -> None:
    text = "588170.SH 华夏半导体ETF、159516.SZ 国泰半导体ETF、588710.SH 华泰半导体ETF"

    result = extract_etf_candidates(text)

    assert [row["code"] for row in result] == ["588170.SH", "159516.SZ", "588710.SH"]
    assert result[0]["name"] == "华夏半导体ETF"


def test_normalize_theme_api_latest_shape() -> None:
    payload = {
        "report_id": "mainline_review_x",
        "result": {
            "generated_at": "2026-06-18 11:04:38 CST",
            "basis_date": "2026-06-17",
            "breadth": {"rows": 1},
            "broad_indexes": [{"code": "000300.SH", "r1": 1.0}],
            "theme_ranking": [
                {
                    "theme": "硬科技电子/半导体",
                    "stage": "主线确认",
                    "evidence_score": 90,
                    "evidence_count": 5,
                    "top_ths": "半导体",
                    "top_etf": "588170.SH 华夏半导体ETF",
                },
                {
                    "theme": "创新药/医药",
                    "stage": "弱势/退潮",
                    "evidence_score": 30,
                    "top_etf": "512010.SH 医药ETF",
                },
            ],
        },
    }

    result = normalize_theme_payload(payload)

    assert result["basis_date"] == "2026-06-17"
    assert result["report_id"] == "mainline_review_x"
    assert result["theme_signals"][0]["score_weight_ratio"] == 90
    assert result["theme_signals"][1]["score_weight_ratio"] == 0.0
    assert result["market_context"]["broad_indexes"][0]["code"] == "000300.SH"


def test_risk_budget_discounts_medium_market_range() -> None:
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

    assert risk_budget_from_market(market_payload) == 36.0


def test_target_allocations_use_four_sleeves() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "equity_position_range": "35%-45%",
                    "market_position_score": 46.98,
                    "sleeve_mix": {"thematic": "0%-8%"},
                }
            }
        }
    }
    theme_payload = {
        "market_context": {
            "broad_indexes": [
                {"code": "000300.SH", "r1": 1.0},
                {"code": "000905.SH", "r1": 2.0},
            ]
        },
        "theme_signals": [
            {
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 60,
                "evidence_score": 90,
                "top_etf": "588170.SH 华夏半导体ETF、159516.SZ 国泰半导体ETF",
            },
            {
                "theme": "AI算力/通信",
                "stage": "观察线",
                "score_weight_ratio": 20,
                "evidence_score": 75,
                "top_etf": "515050.SH 5GETF",
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
    summary = sleeve_summary(rows)

    assert round(budget, 2) == 36.0
    assert round(sum(row["target_weight_ratio"] for row in rows), 6) == 100.0
    rounded_summary = {key: round(value, 4) for key, value in summary.items()}
    assert rounded_summary == {
        "core": 18.0,
        "mainline": 15.0012,
        "thematic": 2.9988,
        "defensive": 64.0,
    }
    assert [row["code"] for row in rows] == [
        "CORE.ASHARE",
        "588170.SH",
        "515050.SH",
        "DEFENSIVE.CASH",
    ]
