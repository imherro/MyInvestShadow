from __future__ import annotations

from shadow_app.allocator import (
    DEFENSIVE_ETF,
    allocation_plan,
    extract_etf_candidates,
    legacy_core_price_point_from_etfs,
    risk_budget_from_market,
    sleeve_summary,
    target_allocations,
)
from shadow_app.pricing import PricePoint
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
    assert result["theme_signals"][0]["etf_score"] is None
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

    price_map = {
        "588170.SH": PricePoint(
            code="588170.SH",
            close=1.1,
            pct_chg=1.2,
            source="test",
            amount=500_000,
            r5=5.0,
            r20=9.0,
            amount_rank=0.9,
            premium_rate=0.1,
        ),
        "159516.SZ": PricePoint(
            code="159516.SZ",
            close=1.0,
            pct_chg=1.0,
            source="test",
            amount=300_000,
            r5=2.0,
            r20=6.0,
            amount_rank=0.7,
            premium_rate=0.2,
        ),
        "515050.SH": PricePoint(
            code="515050.SH",
            close=1.0,
            pct_chg=1.0,
            source="test",
            amount=300_000,
            r5=4.0,
            r20=8.0,
            amount_rank=0.85,
            premium_rate=0.2,
        ),
    }

    budget, rows = target_allocations(market_payload, theme_payload, price_map)
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
        "510300.SH",
        "510500.SH",
        "159915.SZ",
        "588170.SH",
        "515050.SH",
        DEFENSIVE_ETF["code"],
    ]
    assert rows[0]["name"] == "华泰柏瑞沪深300ETF"
    assert round(rows[0]["target_weight_ratio"], 4) == 10.8
    assert rows[-1]["name"] == "银华货币ETF-A"
    assert all(row["instrument_type"] == "etf" for row in rows)
    assert all(row["is_synthetic"] is False for row in rows)
    assert rows[3]["etf_gate_grade"] == "A"
    assert rows[3]["etf_execution_ratio"] == 1.0


def test_etf_gate_moves_missing_data_to_defensive() -> None:
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
    theme_payload = {
        "theme_signals": [
            {
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588170.SH 华夏半导体ETF",
            }
        ]
    }

    plan = allocation_plan(market_payload, theme_payload, {})
    summary = sleeve_summary(plan["targets"])

    assert round(plan["market_risk_budget_ratio"], 2) == 36.0
    assert round(plan["risk_budget_ratio"], 2) == 18.0
    assert summary["mainline"] == 0.0
    assert summary["defensive"] == 82.0
    assert {row["code"] for row in plan["targets"] if row["sleeve"] == "defensive"} == {
        DEFENSIVE_ETF["code"]
    }
    assert plan["etf_gate"][0]["grade"] == "D"
    assert "缺少可验证交易数据" in plan["etf_gate"][0]["reject_reasons"]


def test_etf_gate_discounts_overheated_candidate() -> None:
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
    theme_payload = {
        "theme_signals": [
            {
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588170.SH 热门ETF",
            }
        ]
    }
    price_map = {
        "588170.SH": PricePoint(
            code="588170.SH",
            close=1.0,
            pct_chg=9.0,
            source="test",
            amount=500_000,
            r5=25.0,
            r20=45.0,
            amount_rank=0.9,
        )
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    rows = {row["code"]: row for row in plan["targets"]}

    assert plan["etf_gate"][0]["grade"] == "C"
    assert plan["etf_gate"][0]["execution_ratio"] == 0.45
    assert round(rows["588170.SH"]["target_weight_ratio"], 4) == 6.7505
    assert round(rows[DEFENSIVE_ETF["code"]]["target_weight_ratio"], 4) == 75.2495


def test_legacy_core_return_uses_core_etf_basket() -> None:
    point = legacy_core_price_point_from_etfs(
        {
            "510300.SH": PricePoint("510300.SH", 1.0, 1.0, "test"),
            "510500.SH": PricePoint("510500.SH", 1.0, 2.0, "test"),
            "159915.SZ": PricePoint("159915.SZ", 1.0, 3.0, "test"),
        }
    )

    assert point is not None
    assert point.code == "CORE.ASHARE"
    assert round(point.pct_chg or 0.0, 4) == 1.5
