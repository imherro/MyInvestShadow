from __future__ import annotations

from typing import Any

ETF = dict[str, Any]


def filter_tradeable_etfs(etfs: list[ETF]) -> list[ETF]:
    return [
        etf
        for etf in etfs
        if float((etf.get("gate_report") or {}).get("execution_ratio") or 0.0) > 0.0
    ]
