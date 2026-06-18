from __future__ import annotations

import re
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


def parse_percent_bounds(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", value)]
    if not numbers:
        return None
    if len(numbers) == 1:
        number = clamp(numbers[0], 0.0, 100.0)
        return number, number
    low = clamp(min(numbers[:2]), 0.0, 100.0)
    high = clamp(max(numbers[:2]), 0.0, 100.0)
    return low, high


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
    bounds = parse_percent_bounds(record.get("equity_position_range"))
    if bounds is not None:
        low, high = bounds
        midpoint = (low + high) / 2
    else:
        midpoint = None
    score = record.get("market_position_score")
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        score_value = 45.0

    if midpoint is None:
        if score_value >= 70:
            midpoint = 60.0
        elif score_value >= 55:
            midpoint = 45.0
        elif score_value >= 40:
            midpoint = 35.0
        else:
            midpoint = 20.0

    if score_value >= 70:
        active_ratio = midpoint
    elif score_value >= 55:
        active_ratio = midpoint * 0.95
    elif score_value >= 40:
        active_ratio = midpoint * 0.90
    else:
        active_ratio = midpoint * 0.75

    confidence = str(record.get("confidence") or "").lower()
    if confidence == "low":
        active_ratio *= 0.90

    return clamp(active_ratio, 0.0, 100.0)


def sleeve_targets_from_market(market_payload: dict[str, Any]) -> dict[str, float]:
    active_ratio = risk_budget_from_market(market_payload)
    record = market_record(market_payload)
    try:
        score_value = float(record.get("market_position_score"))
    except (TypeError, ValueError):
        score_value = 45.0

    if score_value >= 70:
        core_share, mainline_share, thematic_share = 0.40, 0.45, 0.15
    elif score_value >= 55:
        core_share, mainline_share, thematic_share = 0.45, 0.45, 0.10
    elif score_value >= 40:
        core_share, mainline_share, thematic_share = 0.50, 0.4167, 0.0833
    else:
        core_share, mainline_share, thematic_share = 0.65, 0.35, 0.0

    thematic_cap = parse_percent_range((record.get("sleeve_mix") or {}).get("thematic"))
    core = active_ratio * core_share
    mainline = active_ratio * mainline_share
    thematic = active_ratio * thematic_share
    if thematic_cap is not None:
        thematic = min(thematic, thematic_cap)
    defensive = max(0.0, 100.0 - core - mainline - thematic)
    return {
        "core": core,
        "mainline": mainline,
        "thematic": thematic,
        "defensive": defensive,
    }


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


def _first_candidate(signal: dict[str, Any]) -> dict[str, str] | None:
    candidates = extract_etf_candidates(signal.get("top_etf"))
    return candidates[0] if candidates else None


def _core_price_point(
    market_payload: dict[str, Any], theme_payload: dict[str, Any]
) -> PricePoint:
    broad_indexes = (
        (theme_payload.get("market_context") or {}).get("broad_indexes")
        or ((theme_payload.get("latest_result") or {}).get("broad_indexes"))
        or []
    )
    weights = {
        "000300.SH": 0.45,
        "000905.SH": 0.25,
        "000001.SH": 0.15,
        "399006.SZ": 0.15,
    }
    weighted = 0.0
    used_weight = 0.0
    fallback_values: list[float] = []
    for row in broad_indexes:
        try:
            r1 = float(row.get("r1"))
        except (TypeError, ValueError):
            continue
        fallback_values.append(r1)
        code = row.get("code")
        if code in weights:
            weighted += r1 * weights[code]
            used_weight += weights[code]
    if used_weight > 0:
        pct_chg = weighted / used_weight
    elif fallback_values:
        pct_chg = sum(fallback_values) / len(fallback_values)
    else:
        pct_chg = None
    return PricePoint(
        code="CORE.ASHARE",
        close=None,
        pct_chg=pct_chg,
        source="theme.market_context.broad_indexes",
    )


def _priced_row(
    *,
    code: str,
    name: str,
    sleeve: str,
    theme: str,
    stage: str,
    target_weight_ratio: float,
    evidence_score: float | None,
    point: PricePoint | None,
    source_note: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "sleeve": sleeve,
        "theme": theme,
        "stage": stage,
        "target_weight_ratio": target_weight_ratio,
        "evidence_score": evidence_score,
        "price": point.close if point else None,
        "pct_chg": point.pct_chg if point else None,
        "source_note": point.source if point else source_note,
    }


def _mainline_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for signal in signals:
        stage = str(signal.get("stage") or "")
        if "主线确认" not in stage and "次主线" not in stage and "强修复" not in stage:
            continue
        if _first_candidate(signal) is None:
            continue
        result.append(signal)
    return sorted(result, key=lambda item: int(item.get("rank") or 999))[:3]


def _thematic_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for signal in signals:
        stage = str(signal.get("stage") or "")
        if "观察" not in stage:
            continue
        try:
            evidence_score = float(signal.get("evidence_score") or 0.0)
        except (TypeError, ValueError):
            evidence_score = 0.0
        if evidence_score < 70:
            continue
        if _first_candidate(signal) is None:
            continue
        candidates.append(signal)
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("evidence_score") or 0.0),
            int(item.get("rank") or 999),
        ),
    )[:1]


def _distribute_budget(
    signals: list[dict[str, Any]], budget: float, price_map: dict[str, PricePoint], sleeve: str
) -> tuple[list[dict[str, Any]], float]:
    if budget <= 0 or not signals:
        return [], budget
    weights = [_signal_weight(signal) * stage_multiplier(signal.get("stage")) for signal in signals]
    total_weight = sum(weights)
    if total_weight <= 0:
        return [], budget

    rows: list[dict[str, Any]] = []
    unused = 0.0
    for signal, weight in zip(signals, weights, strict=False):
        candidate = _first_candidate(signal)
        if candidate is None:
            continue
        target_weight = budget * weight / total_weight
        if target_weight < 1.0:
            unused += target_weight
            continue
        point = price_map.get(candidate["code"])
        rows.append(
            _priced_row(
                code=candidate["code"],
                name=candidate["name"],
                sleeve=sleeve,
                theme=signal.get("theme") or "",
                stage=signal.get("stage") or "",
                target_weight_ratio=target_weight,
                evidence_score=signal.get("evidence_score"),
                point=point,
                source_note="theme_signal.top_etf",
            )
        )
    return rows, unused


def target_allocations(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
    price_map: dict[str, PricePoint] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    price_map = price_map or {}
    sleeves = sleeve_targets_from_market(market_payload)
    signals = theme_payload.get("theme_signals") or []

    rows: list[dict[str, Any]] = []
    core_point = price_map.get("CORE.ASHARE")
    if core_point is None or core_point.pct_chg is None:
        core_point = _core_price_point(market_payload, theme_payload)
    if sleeves["core"] >= 1.0:
        rows.append(
            _priced_row(
                code="CORE.ASHARE",
                name="A股核心宽基组合",
                sleeve="core",
                theme="核心仓位",
                stage=market_record(market_payload).get("market_regime") or "核心底仓",
                target_weight_ratio=sleeves["core"],
                evidence_score=None,
                point=core_point,
                source_note="market broad indexes",
            )
        )

    mainline_rows, _mainline_unused = _distribute_budget(
        _mainline_signals(signals), sleeves["mainline"], price_map, "mainline"
    )
    thematic_rows, _thematic_unused = _distribute_budget(
        _thematic_signals(signals), sleeves["thematic"], price_map, "thematic"
    )
    rows.extend(mainline_rows)
    rows.extend(thematic_rows)

    used_non_defensive = sum(float(row["target_weight_ratio"]) for row in rows)
    defensive_weight = max(0.0, 100.0 - used_non_defensive)
    rows.append(
        _priced_row(
            code="DEFENSIVE.CASH",
            name="防御现金仓",
            sleeve="defensive",
            theme="防御仓位",
            stage="现金/等待",
            target_weight_ratio=defensive_weight,
            evidence_score=None,
            point=PricePoint(
                code="DEFENSIVE.CASH",
                close=1.0,
                pct_chg=0.0,
                source="defensive.cash",
            ),
            source_note="defensive.cash",
        )
    )

    return used_non_defensive, sorted(
        rows,
        key=lambda row: (
            {"core": 0, "mainline": 1, "thematic": 2, "defensive": 3}.get(
                row["sleeve"], 9
            ),
            -float(row["target_weight_ratio"]),
        ),
    )


def allocation_weight_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {row["code"]: float(row.get("target_weight_ratio") or 0.0) for row in rows}


def sleeve_summary(rows: list[dict[str, Any]]) -> dict[str, float]:
    summary = {"core": 0.0, "mainline": 0.0, "thematic": 0.0, "defensive": 0.0}
    for row in rows:
        sleeve = row.get("sleeve")
        if sleeve not in summary:
            continue
        summary[sleeve] += float(row.get("target_weight_ratio") or 0.0)
    return summary
