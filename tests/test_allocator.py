from __future__ import annotations

from shadow_app.allocator import (
    DEFENSIVE_ETF,
    allocation_candidate_codes,
    allocation_plan,
    extract_etf_candidates,
    legacy_core_price_point_from_etfs,
    risk_budget_from_market,
    sleeve_targets_from_market,
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


def test_mainline_ranking_uses_cycle_phase_and_etf_research_candidates() -> None:
    payload = {
        "report_id": "mainline_review_new",
        "result": {
            "basis_date": "2026-06-24",
            "mainline_ranking": [
                {
                    "rank": 1,
                    "theme_id": "hardtech_semiconductor",
                    "theme_name": "硬科技电子/半导体",
                    "cycle_stage": "policy_incubation",
                    "cycle_stage_label": "政策孵化",
                    "cycle_evidence_score": 78,
                    "mainline_score_v6": 0.82,
                }
            ],
            "theme_ranking": [
                {
                    "theme_id": "hardtech_semiconductor",
                    "theme": "硬科技电子/半导体",
                    "stage": "弱势/退潮",
                    "top_etf": "159995.SZ 华夏国证半导体芯片ETF",
                }
            ],
        },
    }
    etf_payload = {
        "basis_date": "2026-06-24",
        "key_results": {
            "primary_output": {
                "items": [
                    {
                        "code": "588200.SH",
                        "name": "嘉实上证科创板芯片ETF",
                        "theme": "半导体芯片",
                        "deep_rating": "A",
                        "deep_score": 86,
                        "shadow_observation_eligible": True,
                    }
                ]
            }
        },
    }

    result = normalize_theme_payload(payload)
    signal = result["theme_signals"][0]
    codes = allocation_candidate_codes({}, result, etf_payload=etf_payload)

    assert signal["stage"] == "观察线/政策孵化"
    assert signal["instrument_preference"] == "etf"
    assert signal["score_weight_ratio"] == 78
    assert signal["top_etf"] == "159995.SZ 华夏国证半导体芯片ETF"
    assert "588200.SH" in codes


def test_risk_budget_respects_market_equity_range_floor() -> None:
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

    plan = allocation_plan(market_payload, {"theme_signals": []}, {})

    assert risk_budget_from_market(market_payload) == 35.0
    assert plan["allocation_policy"]["position_source"] == "market.equity_position_range"
    assert plan["allocation_policy"]["range_clamped"] is True
    assert plan["allocation_policy"]["range_violation"] is False


def test_complete_market_sleeve_mix_drives_position_structure() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "equity_position_range": "20%-40%",
                    "market_position_score": 35.0,
                    "sleeve_mix": {
                        "core": "20%-30%",
                        "offensive": "0%-15%",
                        "defensive": "55%-75%",
                        "thematic": "0%-5%",
                    },
                }
            }
        }
    }

    plan = allocation_plan(market_payload, {"theme_signals": []}, {})

    assert risk_budget_from_market(market_payload) == 35.0
    assert plan["sleeve_targets_before_gate"] == {
        "core": 25.0,
        "mainline": 7.5,
        "thematic": 2.5,
        "defensive": 65.0,
    }
    assert plan["allocation_policy"]["position_source"] == "market.sleeve_mix"
    assert plan["allocation_policy"]["sleeve_source"] == "market.sleeve_mix"
    assert plan["allocation_policy"]["fallback_used"] is False
    assert plan["allocation_policy"]["range_violation"] is False


def test_market_sleeve_allocation_takes_priority_over_fallback_structure() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "equity_position_range": "0%-20%",
                    "market_position_score": 19.95,
                    "allocation_state": "防守期",
                    "sleeve_allocation": [
                        {"key": "core_wide_etf", "target_range": "0%-15%", "midpoint": 7.5},
                        {"key": "mainline_etf", "target_range": "0%-5%", "midpoint": 2.5},
                        {"key": "leader_alpha", "target_range": "0%-0%", "midpoint": 0.0},
                        {"key": "defensive_quality", "target_range": "20%-40%", "midpoint": 30.0},
                        {"key": "cash_like", "target_range": "55%-85%", "midpoint": 70.0},
                    ],
                }
            }
        }
    }

    assert sleeve_targets_from_market(market_payload) == {
        "core": 7.5,
        "mainline": 2.5,
        "thematic": 0.0,
        "defensive": 90.0,
    }
    assert risk_budget_from_market(market_payload) == 10.0

    theme_payload = {
        "theme_signals": [
            {
                "rank": 1,
                "theme": "硬科技电子/半导体",
                "stage": "主线确认",
                "score_weight_ratio": 95,
                "evidence_score": 95,
                "top_etf": "588200.SH 芯片ETF",
            }
        ]
    }
    price_map = {
        "588200.SH": PricePoint("588200.SH", 1.0, 2.0, "test", amount=900_000, r5=8.0, r20=15.0, premium_rate=0.1),
        "512890.SH": PricePoint("512890.SH", 1.0, 0.4, "test", amount=800_000, r5=1.0, r20=3.0, premium_rate=0.1),
        "159201.SZ": PricePoint("159201.SZ", 1.0, 0.3, "test", amount=500_000, r5=0.5, r20=2.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    summary = sleeve_summary(plan["targets"])

    assert plan["allocation_policy"]["position_source"] == "market.sleeve_allocation"
    assert plan["allocation_policy"]["sleeve_source"] == "market.sleeve_allocation"
    assert round(summary["core"], 4) == 7.5
    assert round(summary["mainline"], 4) == 2.5
    assert round(summary["defensive"], 4) == 90.0
    assert plan["gate_universe_audit"]["defensive_quality_selected_count"] == 2
    quality_weight = sum(
        row["target_weight_ratio"]
        for row in plan["targets"]
        if (row.get("etf_gate_components") or {}).get("defensive_layer") == "quality"
    )
    assert round(quality_weight, 4) == 30.0


def test_defensive_market_absorbs_unallocated_thematic_budget() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "equity_position_range": "0%-20%",
                    "market_position_score": 19.95,
                    "allocation_state": "防守期",
                    "sleeve_allocation": [
                        {"key": "core_wide_etf", "target_range": "0%-15%", "midpoint": 7.5},
                        {"key": "mainline_etf", "target_range": "0%-5%", "midpoint": 2.5},
                        {"key": "leader_alpha", "target_range": "0%-4%", "midpoint": 2.0},
                        {"key": "defensive_quality", "target_range": "20%-40%", "midpoint": 30.0},
                        {"key": "cash_like", "target_range": "55%-85%", "midpoint": 70.0},
                    ],
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
                "score_weight_ratio": 95,
                "evidence_score": 95,
                "top_etf": "588200.SH 芯片ETF",
            },
            {
                "rank": 2,
                "theme": "观察方向",
                "stage": "观察线",
                "score_weight_ratio": 90,
                "evidence_score": 90,
                "top_etf": "562500.SH 机器人ETF",
            },
        ]
    }
    price_map = {
        "588200.SH": PricePoint("588200.SH", 1.0, 2.0, "test", amount=900_000, r5=8.0, r20=15.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map)
    summary = sleeve_summary(plan["targets"])

    assert round(plan["market_risk_budget_ratio"], 4) == 12.0
    assert round(plan["risk_budget_ratio"], 4) == 10.0
    assert round(summary["mainline"], 4) == 2.5
    assert round(summary["defensive"], 4) == 90.0
    assert plan["structure_guard_report"]["unallocated_policy"] == "defensive_absorb"
    assert round(plan["structure_guard_report"]["defensive_absorbed_ratio"], 4) == 2.0


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

    assert round(budget, 2) == 35.0
    assert round(sum(row["target_weight_ratio"] for row in rows), 6) == 100.0
    rounded_summary = {key: round(value, 4) for key, value in summary.items()}
    assert rounded_summary == {
        "core": 17.5,
        "mainline": 14.5845,
        "thematic": 2.9155,
        "defensive": 65.0,
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
    assert round(rows[0]["target_weight_ratio"], 4) == 10.5
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
    assert round(thematic_rows[0]["target_weight_ratio"], 4) == 2.9155
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

    assert round(summary["core"], 4) == 21.0
    assert round(summary["mainline"], 4) == 12.25
    assert round(summary["thematic"], 4) == 1.75
    assert round(plan["risk_budget_ratio"], 4) == 35.0


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

    assert round(plan["market_risk_budget_ratio"], 2) == 35.0
    assert round(plan["risk_budget_ratio"], 2) == 17.5
    assert summary["core"] == 17.5
    assert summary["mainline"] == 0.0
    assert summary["defensive"] == 82.5
    assert {row["code"] for row in plan["targets"] if row["sleeve"] == "defensive"} == {
        DEFENSIVE_ETF["code"]
    }
    assert plan["etf_gate"][0]["grade"] == "D"
    assert "缺少可验证交易数据" in plan["etf_gate"][0]["reject_reasons"]
    assert plan["gate_universe_audit"]["pre_gate_universe_size"] == 2
    assert plan["gate_universe_audit"]["post_gate_universe_size"] == 0
    assert round(plan["gate_universe_audit"]["mainline_unallocated_ratio"], 4) == 14.5845
    assert round(plan["gate_universe_audit"]["thematic_unallocated_ratio"], 4) == 2.9155
    assert plan["structure_guard_report"]["safe_mode_triggered"] is True
    assert plan["structure_guard_report"]["active_sum_check"] is True
    assert plan["structure_guard_report"]["total_sum_check"] is True


def test_defensive_sleeve_splits_quality_etf_and_cash_layers() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "market_position_score": 35.0,
                    "sleeve_mix": {
                        "core": "20%",
                        "mainline": "0%",
                        "thematic": "0%",
                        "defensive": "80%",
                    },
                }
            }
        }
    }
    etf_payload = {
        "basis_date": "2026-06-24",
        "key_results": {
            "primary_output": {
                "items": [
                    {
                        "code": "512890.SH",
                        "name": "华泰柏瑞中证红利低波动ETF",
                        "valuation_model_type": "factor_defensive",
                        "sleeve_key": "defensive_quality",
                        "category_key": "红利低波",
                        "deep_rating": "A",
                        "deep_score": 88,
                        "shadow_observation_eligible": True,
                        "scores": {
                            "liquidity_score": 80,
                            "factor_premium_score": 82,
                            "portfolio_role_score": 86,
                            "tracking_score": 78,
                        },
                    },
                    {
                        "code": "159201.SZ",
                        "name": "华夏国证自由现金流ETF",
                        "valuation_model_type": "factor_defensive",
                        "sleeve_key": "defensive_quality",
                        "category_key": "自由现金流",
                        "deep_rating": "A",
                        "deep_score": 82,
                        "shadow_observation_eligible": True,
                        "scores": {
                            "liquidity_score": 75,
                            "factor_premium_score": 80,
                            "portfolio_role_score": 82,
                            "tracking_score": 76,
                        },
                    },
                ]
            }
        },
    }
    price_map = {
        "512890.SH": PricePoint("512890.SH", 1.0, 0.4, "test", amount=800_000, r5=1.0, r20=3.0, premium_rate=0.1),
        "159201.SZ": PricePoint("159201.SZ", 1.0, 0.3, "test", amount=500_000, r5=0.5, r20=2.0, premium_rate=0.1),
    }

    plan = allocation_plan(market_payload, {"theme_signals": []}, price_map, etf_payload=etf_payload)
    defensive_rows = [row for row in plan["targets"] if row["sleeve"] == "defensive"]
    quality_rows = [
        row
        for row in defensive_rows
        if (row.get("etf_gate_components") or {}).get("defensive_layer") == "quality"
    ]
    cash_row = [row for row in defensive_rows if row["code"] == DEFENSIVE_ETF["code"]][0]

    assert {row["code"] for row in quality_rows} == {"512890.SH", "159201.SZ"}
    assert round(sum(row["target_weight_ratio"] for row in quality_rows), 4) == 25.0
    assert round(cash_row["target_weight_ratio"], 4) == 55.0
    assert plan["gate_universe_audit"]["defensive_quality_selected_count"] == 2


def test_stock_research_can_fill_small_thematic_leader_sleeve() -> None:
    market_payload = {
        "results": {
            "market_score": {
                "record": {
                    "market_position_score": 55.0,
                    "sleeve_mix": {
                        "core": "20%",
                        "mainline": "0%",
                        "thematic": "8%",
                        "defensive": "72%",
                    },
                }
            }
        }
    }
    theme_payload = {
        "theme_signals": [
            {
                "rank": 1,
                "theme": "机器人",
                "stage": "主线确认/资金收敛",
                "instrument_preference": "leader",
                "score_weight_ratio": 85,
                "evidence_score": 88,
                "top_etf": "",
            }
        ]
    }
    stock_payload = {
        "basis_date": "2026-06-24",
        "stocks": [
            {
                "leader": {
                    "code": "688999.SH",
                    "name": "机器人龙头",
                    "theme": "机器人",
                    "deep_rating": "A",
                    "deep_score": 88,
                    "shadow_observation_eligible": True,
                    "scores": {
                        "theme_binding": 90,
                        "evidence_quality": 88,
                        "trading_structure": 62,
                        "financial_quality": 78,
                        "valuation_safety": 65,
                    },
                    "market": {"turnover_rate": 4.8},
                }
            }
        ],
    }
    price_map = {
        "688999.SH": PricePoint("688999.SH", 30.0, 2.0, "test", amount=700_000, r5=6.0, r20=12.0),
    }

    plan = allocation_plan(market_payload, theme_payload, price_map, stock_payload=stock_payload)
    stock_rows = [row for row in plan["targets"] if row.get("instrument_type") == "stock"]

    assert [row["code"] for row in stock_rows] == ["688999.SH"]
    assert stock_rows[0]["sleeve"] == "thematic"
    assert stock_rows[0]["target_weight_ratio"] <= 6.0
    assert plan["gate_universe_audit"]["stock_selected_count"] == 1


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

    assert plan["etf_gate"][0]["grade"] == "B"
    assert plan["etf_gate"][0]["pre_gate_execution_ratio"] == 0.75
    assert plan["etf_gate"][0]["execution_ratio"] == 1.0
    assert round(rows["588170.SH"]["target_weight_ratio"], 4) == 17.5
    assert round(rows[DEFENSIVE_ETF["code"]]["target_weight_ratio"], 4) == 65.0
    assert round(plan["gate_universe_audit"]["thematic_unallocated_ratio"], 4) == 2.9155
    assert plan["structure_guard_report"]["safe_mode_triggered"] is False
    assert plan["structure_guard_report"]["redistributed_ratio"]["mainline"] == 2.9155


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
