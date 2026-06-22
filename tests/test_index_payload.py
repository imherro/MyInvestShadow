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
        "allocations": [{"code": "510300.SH", "target_weight_ratio": 8.1}],
        "rebalance_history": [
            {
                "basis_date": "2026-06-17",
                "changes": [{"code": "510300.SH", "action": "new"}],
            }
        ],
        "run_payload": {
            "decision_trace": {
                "etf_gate_summary": {"reviewed_count": 1, "selected_count": 1},
                "etf_gate": [{"code": "588170.SH", "grade": "A"}],
            }
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
    assert payload["etf_gate_summary"]["reviewed_count"] == 1
    assert payload["etf_gate"][0]["grade"] == "A"
    assert payload["rebalance_history"][0]["basis_date"] == "2026-06-17"
    assert payload["links"]["rebalance_history"] == "/api/rebalance-history"
    assert payload["links"]["full_state"] == "/api/latest"
    assert "run_payload" not in payload
    assert "payload_json" not in payload["run"]
