from __future__ import annotations

from etf.gate_filter import filter_tradeable_etfs


def test_filter_tradeable_etfs_keeps_positive_execution_ratio() -> None:
    etfs = [
        {"code": "A", "gate_report": {"execution_ratio": 1.0}},
        {"code": "B", "gate_report": {"execution_ratio": 0.45}},
        {"code": "C", "gate_report": {"execution_ratio": 0.0}},
    ]

    result = filter_tradeable_etfs(etfs)

    assert [row["code"] for row in result] == ["A", "B"]
