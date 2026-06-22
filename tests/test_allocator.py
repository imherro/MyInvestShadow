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


def test_risk_budget_uses_single_position_sizer() -> None:
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

    assert risk_budget_from_market(market_payload) == 30.0


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
            pct_chg=2.0,
            source="test",
            amount=300_000,
            r5=8.0,
            r20=15.0,
            amount_rank=0.85,
            premium_rate=0.2,
        ),
    }

    budget, rows = target_allocations(market_payload, theme_payload, price_map)
    summary = sleeve_summary(rows)

    assert round(budget, 2) == 30.0
    assert round(sum(row["target_weight_ratio"] for row in rows), 6) == 100.0
    rounded_summary = {key: round(value, 4) for key, value in summary.items()}
    assert rounded_summary == {
        "core": 15.0,
        "mainline": 12.501,
        "thematic": 2.499,
        "defensive": 70.0,
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
    assert round(rows[0]["target_weight_ratio"], 4) == 9.0
    assert rows[-1]["name"] == "银华货币ETF-A"
    assert all(row["instrument_type"] == "etf" for row in rows)
    assert all(row["is_synthetic"] is False for row in rows)
    assert rows[3]["etf_gate_grade"] == "A"
    assert rows[3]["etf_execution_ratio"] == 1.0


def test_mainline_gate_grade_factor_participates_in_weight() -> None:
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
                "stage": "次主线/强修复",
                "score_weight_ratio": 83,
                "evidence_score": 83,
                "top_etf": "588170.SH 半导体ETF",
            },
            {
                "theme": "新能源/电力设备",
                "stage": "次主线/强修复",
                "score_weight_ratio": 73,
                "evidence_score": 73,
                "top_etf": "159326.SZ 电网设备ETF",
            },
        ]
    }
    price_map = {
        "588170.SH": PricePoint(
            code="588170.SH",
            close=1.0,
            pct_chg=2.0,
            source="test",
            amount=1_000_000,
            amount_rank=1.0,
        ),
        "159326.SZ": PricePoint(
            code="159326.SZ",
            close=1.0,
            pct_chg=2.0,
            source="test",
            amount=1_000_000,
            amount_rank=1.0,
            premium_rate=0.1,
        ),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    rows = {row["code"]: row for row in plan["targets"]}

    assert rows["588170.SH"]["etf_gate_grade"] == "B"
    assert rows["159326.SZ"]["etf_gate_grade"] == "A"
    assert rows["588170.SH"]["etf_gate_components"]["gate_weight_factor"] == 0.85
    assert rows["159326.SZ"]["etf_gate_components"]["gate_weight_factor"] == 1.0
    assert (
        rows["159326.SZ"]["target_weight_ratio"]
        > rows["588170.SH"]["target_weight_ratio"]
    )


def test_thematic_prefers_unheld_strong_market_performance() -> None:
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
                "rank": 1,
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588170.SH 半导体ETF",
            },
            {
                "rank": 2,
                "theme": "AI算力/通信",
                "stage": "主线确认",
                "score_weight_ratio": 85,
                "evidence_score": 85,
                "top_etf": "515050.SH 5GETF",
            },
            {
                "rank": 3,
                "theme": "机器人",
                "stage": "主线确认",
                "score_weight_ratio": 80,
                "evidence_score": 80,
                "top_etf": "562500.SH 机器人ETF",
            },
            {
                "rank": 4,
                "theme": "工业母机",
                "stage": "主线确认",
                "score_weight_ratio": 92,
                "evidence_score": 92,
                "top_etf": "159663.SZ 机床ETF",
            },
            {
                "rank": 5,
                "theme": "消费观察",
                "stage": "观察线",
                "score_weight_ratio": 95,
                "evidence_score": 95,
                "top_etf": "512690.SH 消费ETF",
            },
        ]
    }
    price_map = {
        "588170.SH": PricePoint("588170.SH", 1.0, 1.0, "test", amount=500_000, r5=5.0, r20=9.0, premium_rate=0.1),
        "515050.SH": PricePoint("515050.SH", 1.0, 1.0, "test", amount=500_000, r5=4.0, r20=8.0, premium_rate=0.1),
        "562500.SH": PricePoint("562500.SH", 1.0, 1.0, "test", amount=500_000, r5=3.0, r20=7.0, premium_rate=0.1),
        "159663.SZ": PricePoint("159663.SZ", 1.0, 2.0, "test", amount=500_000, r5=12.0, r20=20.0, premium_rate=0.1),
        "512690.SH": PricePoint("512690.SH", 1.0, -1.0, "test", amount=500_000, r5=-2.0, r20=-4.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    thematic_rows = [row for row in plan["targets"] if row["sleeve"] == "thematic"]

    assert [row["code"] for row in thematic_rows] == ["159663.SZ"]
    assert round(thematic_rows[0]["target_weight_ratio"], 4) == 2.499
    assert "主题仓位按市场表现优先" in thematic_rows[0]["etf_gate_reasons"]


def test_thematic_tolerates_non_numeric_rank() -> None:
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
                "rank": "R1",
                "theme": "观察强势方向",
                "stage": "观察线",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "562500.SH 机器人ETF",
            }
        ]
    }
    price_map = {
        "562500.SH": PricePoint(
            "562500.SH",
            1.0,
            1.0,
            "test",
            amount=500_000,
            r5=6.0,
            r20=12.0,
            premium_rate=0.1,
        )
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    thematic_rows = [row for row in plan["targets"] if row["sleeve"] == "thematic"]

    assert [row["code"] for row in thematic_rows] == ["562500.SH"]


def test_unselected_observation_gate_stays_watch_backup() -> None:
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
                "rank": 1,
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588170.SH 半导体ETF",
            },
            {
                "rank": 3,
                "theme": "低动能观察",
                "stage": "观察线",
                "score_weight_ratio": 85,
                "evidence_score": 85,
                "top_etf": "562500.SH 机器人ETF",
            },
        ]
    }
    price_map = {
        "588170.SH": PricePoint("588170.SH", 1.0, 1.0, "test", amount=500_000, r5=5.0, r20=9.0, premium_rate=0.1),
        "562500.SH": PricePoint("562500.SH", 1.0, -2.0, "test", amount=500_000, r5=-4.0, r20=-8.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    watch_row = [row for row in plan["etf_gate"] if row["theme"] == "低动能观察"][0]

    assert watch_row["sleeve"] == "watch"
    assert watch_row["selected"] is False
    assert "观察线保留备选" in watch_row["reasons"]


def test_gate_keeps_largest_amount_etf_per_direction() -> None:
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
                "rank": 1,
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588170.SH 半导体ETF、159516.SZ 芯片ETF",
            }
        ]
    }
    price_map = {
        "588170.SH": PricePoint("588170.SH", 1.0, 1.0, "test", amount=100_000, r5=8.0, r20=15.0, premium_rate=0.1),
        "159516.SZ": PricePoint("159516.SZ", 1.0, 1.0, "test", amount=600_000, r5=6.0, r20=12.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    mainline_rows = [row for row in plan["targets"] if row["sleeve"] == "mainline"]
    mainline_gate = [row for row in plan["etf_gate"] if row["sleeve"] == "mainline"]

    assert [row["code"] for row in mainline_rows] == ["159516.SZ"]
    assert [row["code"] for row in mainline_gate] == ["159516.SZ"]
    assert mainline_gate[0]["grade"] == "A"
    assert mainline_gate[0]["direction_representative_code"] == "159516.SZ"


def test_thematic_excludes_direction_already_held_by_mainline() -> None:
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
                "rank": 1,
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588200.SH 芯片ETF、159995.SZ 半导体ETF",
            }
        ]
    }
    price_map = {
        "588200.SH": PricePoint("588200.SH", 1.0, 1.0, "test", amount=900_000, r5=5.0, r20=10.0, premium_rate=0.1),
        "159995.SZ": PricePoint("159995.SZ", 1.0, 4.0, "test", amount=500_000, r5=15.0, r20=25.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    thematic_rows = [row for row in plan["targets"] if row["sleeve"] == "thematic"]
    thematic_gate = [row for row in plan["etf_gate"] if row["sleeve"] == "thematic"]

    assert thematic_rows == []
    assert thematic_gate == []
    assert "159995.SZ" not in [row["code"] for row in plan["targets"]]


def test_gate_keeps_observation_direction_as_watch_backup() -> None:
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
                "rank": 1,
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588200.SH 芯片ETF",
            },
            {
                "rank": 4,
                "theme": "消费观察",
                "stage": "观察线",
                "score_weight_ratio": 65,
                "evidence_score": 65,
                "top_etf": "512690.SH 消费ETF、159928.SZ 消费ETF",
            },
        ]
    }
    price_map = {
        "588200.SH": PricePoint("588200.SH", 1.0, 1.0, "test", amount=900_000, r5=5.0, r20=10.0, premium_rate=0.1),
        "512690.SH": PricePoint("512690.SH", 1.0, -1.0, "test", amount=300_000, r5=-2.0, r20=-4.0, premium_rate=0.1),
        "159928.SZ": PricePoint("159928.SZ", 1.0, -0.5, "test", amount=700_000, r5=-1.0, r20=-3.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    gate_rows = {row["theme"]: row for row in plan["etf_gate"]}

    assert gate_rows["消费观察"]["code"] == "159928.SZ"
    assert gate_rows["消费观察"]["sleeve"] == "watch"
    assert gate_rows["消费观察"]["selected"] is False
    assert "观察线保留备选" in gate_rows["消费观察"]["reasons"]


def test_weak_market_keeps_sub_one_percent_thematic_after_pre_gate() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "equity_position_range": "35%-45%",
                    "market_position_score": 35.0,
                }
            }
        }
    }
    theme_payload = {
        "theme_signals": [
            {
                "rank": 1,
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "588170.SH 半导体ETF",
            },
            {
                "rank": 2,
                "theme": "AI算力/通信",
                "stage": "观察线",
                "score_weight_ratio": 85,
                "evidence_score": 85,
                "top_etf": "515050.SH 5GETF",
            },
        ]
    }
    price_map = {
        "588170.SH": PricePoint("588170.SH", 1.0, 1.0, "test", amount=500_000, r5=5.0, r20=9.0, premium_rate=0.1),
        "515050.SH": PricePoint("515050.SH", 1.0, 2.0, "test", amount=500_000, r5=8.0, r20=15.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    summary = sleeve_summary(plan["targets"])

    assert round(summary["core"], 4) == 9.0
    assert round(summary["mainline"], 4) == 5.25
    assert round(summary["thematic"], 4) == 0.75
    assert round(plan["risk_budget_ratio"], 4) == 15.0


def test_structure_guard_safe_mode_keeps_missing_data_budget_defensive() -> None:
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

    assert round(plan["market_risk_budget_ratio"], 2) == 30.0
    assert round(plan["risk_budget_ratio"], 2) == 15.0
    assert summary["core"] == 15.0
    assert summary["mainline"] == 0.0
    assert summary["defensive"] == 85.0
    assert {row["code"] for row in plan["targets"] if row["sleeve"] == "defensive"} == {
        DEFENSIVE_ETF["code"]
    }
    assert plan["etf_gate"][0]["grade"] == "D"
    assert "缺少可验证交易数据" in plan["etf_gate"][0]["reject_reasons"]
    assert plan["gate_universe_audit"]["pre_gate_universe_size"] == 2
    assert plan["gate_universe_audit"]["post_gate_universe_size"] == 0
    assert round(plan["gate_universe_audit"]["mainline_unallocated_ratio"], 4) == 12.501
    assert round(plan["gate_universe_audit"]["thematic_unallocated_ratio"], 4) == 2.499
    assert plan["structure_guard_report"]["safe_mode_triggered"] is True
    assert plan["structure_guard_report"]["active_sum_check"] is True
    assert plan["structure_guard_report"]["total_sum_check"] is True


def test_pre_gate_keeps_tradeable_overheated_candidate_without_discount() -> None:
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
    assert plan["etf_gate"][0]["pre_gate_execution_ratio"] == 0.45
    assert plan["etf_gate"][0]["execution_ratio"] == 1.0
    assert round(rows["588170.SH"]["target_weight_ratio"], 4) == 15.0
    assert round(rows[DEFENSIVE_ETF["code"]]["target_weight_ratio"], 4) == 70.0
    assert round(plan["gate_universe_audit"]["thematic_unallocated_ratio"], 4) == 2.499
    assert plan["structure_guard_report"]["safe_mode_triggered"] is False
    assert plan["structure_guard_report"]["redistributed_ratio"]["mainline"] == 2.499


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
