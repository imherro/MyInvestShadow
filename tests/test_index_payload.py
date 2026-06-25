from __future__ import annotations

from shadow_app.service import build_index_payload


def test_build_index_payload_is_homepage_focused() -> None:
    state = {
        "run": {
            "id": 7,
            "run_at": "2026-06-18T12:00:00+08:00",
            "basis_date": "2026-06-17",
            "market_basis_date": "2026-06-17",
            "theme_report_id": "mainline_review_x",
            "market_regime": "结构性偏强但分歧较大",
            "risk_budget_ratio": 36.0,
            "cash_ratio": 64.0,
            "nav": 1.0123,
            "daily_return_ratio": 0.001,
            "reason": "test",
            "payload_json": "{}",
        },
        "sleeve_summary": {
            "core": 18.0,
            "mainline": 15.0,
            "thematic": 3.0,
            "defensive": 64.0,
        },
        "nav_curve": [{"basis_date": "2026-06-17", "nav": 1.0123}],
        "benchmark_curve": [
            {
                "code": "510300.SH",
                "name": "华泰柏瑞沪深300ETF",
                "points": [{"basis_date": "2026-06-17", "close": 4.0, "normalized": 1.0}],
            }
        ],
        "allocations": [
            {"code": "510300.SH", "target_weight_ratio": 10.8},
            {
                "code": "512890.SH",
                "sleeve": "defensive",
                "target_weight_ratio": 20.0,
                "etf_gate_components": {"defensive_layer": "quality"},
            },
            {
                "code": "511880.SH",
                "sleeve": "defensive",
                "target_weight_ratio": 44.0,
                "etf_gate_components": {},
            },
        ],
        "rebalance_history": [
            {
                "basis_date": "2026-06-17",
                "changes": [{"code": "510300.SH", "action": "new"}],
            }
        ],
        "run_payload": {
            "optional_source_policy": {"etf_used": True, "stock_used": False},
            "market_constraints": {
                "allocation_state": "防守期",
                "market_position_score": 19.95,
                "equity_position_range": "0%-20%",
                "risk_caps": [{"reason": "strong_index_weak_breadth"}],
            },
            "decision_trace": {
                "etf_gate_summary": {"reviewed_count": 1, "selected_count": 1},
                "etf_gate": [{"code": "588170.SH", "grade": "A"}],
                "allocation_policy": {
                    "position_source": "market.sleeve_mix",
                    "range_violation": False,
                },
                "stock_gate": [{"code": "688999.SH", "selected": True}],
                "defensive_quality_gate": [{"code": "512890.SH", "selected": True}],
            },
        },
        "source_status": [{"source": "theme", "ok": 1}],
    }

    payload = build_index_payload(state)

    assert payload["page"]["title"] == "MyInvestShadow"
    assert payload["metrics"]["active_weight_ratio"] == 36.0
    assert payload["sleeves"][0] == {
        "key": "core",
        "label": "核心仓位",
        "weight_ratio": 18.0,
    }
    assert payload["allocations"][0]["code"] == "510300.SH"
    assert payload["defensive_layers"] == [
        {"key": "defensive_quality", "label": "收益防御", "weight_ratio": 20.0},
        {"key": "cash_like", "label": "现金防御", "weight_ratio": 44.0},
    ]
    assert payload["optional_source_policy"]["etf_used"] is True
    assert payload["market_constraints"]["allocation_state"] == "防守期"
    assert payload["market_constraints"]["risk_caps"][0]["reason"] == "strong_index_weak_breadth"
    assert payload["stock_gate"][0]["selected"] is True
    assert payload["defensive_quality_gate"][0]["code"] == "512890.SH"
    assert payload["benchmark_curve"][0]["code"] == "510300.SH"
    assert payload["benchmark_curve"][0]["points"][0]["normalized"] == 1.0
    assert payload["etf_gate_summary"]["reviewed_count"] == 1
    assert payload["etf_gate"][0]["grade"] == "A"
    assert payload["allocation_policy"]["position_source"] == "market.sleeve_mix"
    assert payload["allocation_policy"]["range_violation"] is False
    assert payload["rebalance_history"][0]["basis_date"] == "2026-06-17"
    assert payload["links"]["rebalance_history"] == "/api/rebalance-history"
    assert payload["links"]["full_state"] == "/api/latest"
    assert "run_payload" not in payload
    assert "payload_json" not in payload["run"]


def test_build_index_payload_uses_top_level_gate_fields() -> None:
    state = {
        "run": {"id": 8, "basis_date": "2026-06-18"},
        "sleeve_summary": {},
        "etf_gate_summary": {"reviewed_count": 2, "selected_count": 1},
        "etf_gate": [
            {"code": "588200.SH", "grade": "A"},
            {"code": "562500.SH", "grade": "B"},
        ],
    }

    payload = build_index_payload(state)

    assert payload["etf_gate_summary"]["reviewed_count"] == 2
    assert [row["code"] for row in payload["etf_gate"]] == ["588200.SH", "562500.SH"]
