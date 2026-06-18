from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from .pricing import PricePoint

ETF_CODE_RE = re.compile(r"\b(?P<code>\d{6}\.(?:SH|SZ|BJ))\b\s*(?P<name>[^、,，;；]*)")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_percent_range(value: str | None) -> float | None:
    if not value:
        return None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", value)]
    if not numbers:
        return None
    if len(numbers) == 1:
        return clamp(numbers[0], 0.0, 100.0)
    return clamp(sum(numbers[:2]) / 2, 0.0, 100.0)


def extract_etf_candidates(text: str | None) -> list[dict[str, str]]:
    if not text:
        return []
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for match in ETF_CODE_RE.finditer(text):
        code = match.group("code")
        if code in seen:
            continue
        seen.add(code)
        name = match.group("name").strip(" -_/，,、;；")
        result.append({"code": code, "name": name or code})
    return result


def market_record(market_payload: dict[str, Any]) -> dict[str, Any]:
    return (
        ((market_payload.get("results") or {}).get("market_score") or {}).get("record")
        or {}
    )


def risk_budget_from_market(market_payload: dict[str, Any]) -> float:
    record = market_record(market_payload)
    from_range = parse_percent_range(record.get("equity_position_range"))
    if from_range is not None:
        return from_range
    score = record.get("market_position_score")
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        return 30.0
    if score_value >= 70:
        return 60.0
    if score_value >= 55:
        return 45.0
    if score_value >= 40:
        return 35.0
    return 20.0


def stage_multiplier(stage: str | None) -> float:
    label = stage or ""
    if "主线确认" in label:
        return 1.0
    if "次主线" in label or "强修复" in label:
        return 0.85
    if "观察" in label:
        return 0.45
    if "弱势" in label or "退潮" in label:
        return 0.0
    return 0.5


def candidate_splits(count: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1:
        return [1.0]
    if count == 2:
        return [0.65, 0.35]
    return [0.55, 0.30, 0.15][:count]


def _signal_weight(signal: dict[str, Any]) -> float:
    raw = signal.get("score_weight_ratio", signal.get("evidence_score", 0.0))
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.0


def target_allocations(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
    price_map: dict[str, PricePoint] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    risk_budget = risk_budget_from_market(market_payload)
    signals = theme_payload.get("theme_signals") or []
    prepared: list[dict[str, Any]] = []
    for signal in signals:
        candidates = extract_etf_candidates(signal.get("top_etf"))[:3]
        if not candidates:
            continue
        adjusted_weight = _signal_weight(signal) * stage_multiplier(signal.get("stage"))
        if adjusted_weight <= 0:
            continue
        prepared.append(
            {
                "signal": signal,
                "candidates": candidates,
                "adjusted_weight": adjusted_weight,
            }
        )

    total_adjusted = sum(item["adjusted_weight"] for item in prepared)
    if total_adjusted <= 0:
        return risk_budget, []

    merged: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in prepared:
        signal = item["signal"]
        theme_budget = risk_budget * item["adjusted_weight"] / total_adjusted
        splits = candidate_splits(len(item["candidates"]))
        for candidate, split in zip(item["candidates"], splits, strict=False):
            code = candidate["code"]
            target_weight = theme_budget * split
            point = (price_map or {}).get(code)
            if code not in merged:
                merged[code] = {
                    "code": code,
                    "name": candidate["name"],
                    "theme": signal.get("theme") or "",
                    "stage": signal.get("stage") or "",
                    "target_weight_ratio": 0.0,
                    "evidence_score": signal.get("evidence_score"),
                    "price": point.close if point else None,
                    "pct_chg": point.pct_chg if point else None,
                    "source_note": point.source if point else "theme_signal.top_etf",
                }
            else:
                existing = merged[code]
                if signal.get("theme") and signal["theme"] not in existing["theme"]:
                    existing["theme"] = f"{existing['theme']} / {signal['theme']}"
            merged[code]["target_weight_ratio"] += target_weight

    return risk_budget, list(merged.values())


def allocation_weight_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {row["code"]: float(row.get("target_weight_ratio") or 0.0) for row in rows}


def compare_actual_to_target(
    actual_holdings: list[dict[str, Any]], target_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    targets = {row["code"]: row for row in target_rows}
    actuals = {row["code"]: row for row in actual_holdings if row.get("code")}
    codes = sorted(set(targets) | set(actuals))
    result: list[dict[str, Any]] = []
    for code in codes:
        target = targets.get(code, {})
        actual = actuals.get(code, {})
        target_weight = float(target.get("target_weight_ratio") or 0.0)
        actual_weight = float(actual.get("weight_ratio") or 0.0)
        diff = actual_weight - target_weight
        if abs(diff) <= 0.5:
            status = "基本贴合"
        elif target_weight == 0:
            status = "不在影子目标"
        elif actual_weight < target_weight:
            status = "低于影子目标"
        else:
            status = "高于影子目标"
        result.append(
            {
                "code": code,
                "name": actual.get("name") or target.get("name") or code,
                "theme": target.get("theme") or actual.get("theme") or "",
                "actual_weight_ratio": actual_weight,
                "target_weight_ratio": target_weight,
                "difference_ratio": diff,
                "status": status,
            }
        )
    return result
