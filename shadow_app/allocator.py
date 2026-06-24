from __future__ import annotations

import re
from typing import Any

from etf.gate_filter import filter_tradeable_etfs
from portfolio.position_sizer import compute_target_position
from portfolio.structure_guard import enforce_structure_constraints

from .etf_gate import evaluate_etf_gate, gate_weight_factor
from .etf_research import (
    defensive_quality_candidates,
    enrich_theme_signals_with_etf_research,
    etf_research_by_code,
)
from .pricing import PricePoint
from .stock_research import extract_stock_candidates, stock_gate_score, theme_matches_stock

ETF_CODE_RE = re.compile(r"\b(?P<code>\d{6}\.(?:SH|SZ|BJ))\b\s*(?P<name>[^、,，;；]*)")
SYNTHETIC_INSTRUMENTS = {
    "CORE.ASHARE": {
        "display_code": "内部组合",
        "instrument_type": "synthetic_core",
        "is_synthetic": True,
    },
    "DEFENSIVE.CASH": {
        "display_code": "现金仓",
        "instrument_type": "synthetic_cash",
        "is_synthetic": True,
    },
}
CORE_ETF_BASKET = (
    {
        "code": "510300.SH",
        "name": "华泰柏瑞沪深300ETF",
        "theme": "核心-沪深300",
        "weight": 0.60,
    },
    {
        "code": "510500.SH",
        "name": "南方中证500ETF",
        "theme": "核心-中证500",
        "weight": 0.30,
    },
    {
        "code": "159915.SZ",
        "name": "易方达创业板ETF",
        "theme": "核心-创业板",
        "weight": 0.10,
    },
)
DEFENSIVE_ETF = {
    "code": "511880.SH",
    "name": "银华货币ETF-A",
    "theme": "防御仓位",
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_percent_range(value: str | None) -> float | None:
    bounds = parse_percent_range_bounds(value)
    if bounds is None:
        return None
    low, high = bounds
    return clamp((low + high) / 2, 0.0, 100.0)


def parse_percent_range_bounds(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", value)]
    if not numbers:
        return None
    if len(numbers) == 1:
        number = clamp(numbers[0], 0.0, 100.0)
        return number, number
    low, high = sorted(numbers[:2])
    return clamp(low, 0.0, 100.0), clamp(high, 0.0, 100.0)


def extract_etf_candidates(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
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


def _market_score_value(record: dict[str, Any]) -> float:
    try:
        return float(record.get("market_position_score"))
    except (TypeError, ValueError):
        return 45.0


def _confidence_value(record: dict[str, Any]) -> float:
    raw = record.get("confidence")
    if raw is None or raw == "":
        return 1.0
    try:
        number = float(raw)
        if number > 1.0:
            number /= 100.0
        return clamp(number, 0.0, 1.0)
    except (TypeError, ValueError):
        label = str(raw).strip().lower()
    return {
        "high": 1.0,
        "medium": 0.70,
        "mid": 0.70,
        "normal": 0.70,
        "low": 0.35,
    }.get(label, 1.0)


def _regime_value(record: dict[str, Any]) -> str | None:
    raw = record.get("risk_regime") or record.get("regime") or record.get("market_regime")
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in {"risk_on", "risk-on", "risk on"}:
        return "risk_on"
    if value in {"risk_off", "risk-off", "risk off"}:
        return "risk_off"
    if value in {"neutral", "中性"}:
        return "neutral"
    if any(token in value for token in ("进攻", "强势", "上行", "riskon")):
        return "risk_on"
    if any(token in value for token in ("防御", "弱势", "退潮", "riskoff")):
        return "risk_off"
    return "neutral"


def _official_equity_position_range(record: dict[str, Any]) -> str | None:
    return (
        record.get("recommended_equity_position_range")
        or record.get("equity_position_range")
        or record.get("base_equity_position_range")
    )


def _complete_market_sleeve_mix(record: dict[str, Any]) -> dict[str, float] | None:
    mix = record.get("sleeve_mix")
    if not isinstance(mix, dict):
        return None
    core = parse_percent_range(mix.get("core"))
    mainline = parse_percent_range(mix.get("mainline") or mix.get("offensive"))
    thematic = parse_percent_range(mix.get("thematic"))
    defensive = parse_percent_range(mix.get("defensive"))
    if None in {core, mainline, thematic, defensive}:
        return None
    return {
        "core": float(core),
        "mainline": float(mainline),
        "thematic": float(thematic),
        "defensive": float(defensive),
    }


def _scale_active_sleeves(
    sleeves: dict[str, float], target_active: float
) -> dict[str, float]:
    active = sum(float(sleeves[key]) for key in ("core", "mainline", "thematic"))
    if active <= 0.0:
        return {
            "core": 0.0,
            "mainline": 0.0,
            "thematic": 0.0,
            "defensive": 100.0,
        }
    scale = target_active / active
    core = float(sleeves["core"]) * scale
    mainline = float(sleeves["mainline"]) * scale
    thematic = float(sleeves["thematic"]) * scale
    return {
        "core": core,
        "mainline": mainline,
        "thematic": thematic,
        "defensive": max(0.0, 100.0 - core - mainline - thematic),
    }


def _position_sizing_from_market(market_payload: dict[str, Any]) -> dict[str, Any]:
    record = market_record(market_payload)
    score_value = _market_score_value(record)
    confidence = _confidence_value(record)
    regime = _regime_value(record)
    range_text = _official_equity_position_range(record)
    range_bounds = parse_percent_range_bounds(range_text)
    market_sleeves = _complete_market_sleeve_mix(record)
    fallback_sizing = compute_target_position(score_value, confidence, regime)
    fallback_position = float(fallback_sizing["final_position"])

    source = "shadow_fallback_score_bands"
    final_position = fallback_position
    range_clamped = False
    sleeve_mix_active: float | None = None

    if market_sleeves is not None:
        sleeve_mix_active = (
            market_sleeves["core"] + market_sleeves["mainline"] + market_sleeves["thematic"]
        ) / 100.0
        final_position = sleeve_mix_active
        source = "market.sleeve_mix"
    elif range_bounds is not None:
        final_position = fallback_position
        source = "market.equity_position_range"

    if range_bounds is not None:
        low, high = range_bounds
        before = final_position
        final_position = clamp(final_position * 100.0, low, high) / 100.0
        range_clamped = abs(final_position - before) > 1e-9

    return {
        "final_position": round(final_position, 6),
        "components": {
            **fallback_sizing["components"],
            "source": source,
            "fallback_score_position": fallback_position,
            "sleeve_mix_active": (
                round(sleeve_mix_active, 6) if sleeve_mix_active is not None else None
            ),
            "equity_range_low": range_bounds[0] / 100.0 if range_bounds else None,
            "equity_range_high": range_bounds[1] / 100.0 if range_bounds else None,
            "range_clamped": range_clamped,
            "fallback_used": source == "shadow_fallback_score_bands",
        },
        "inputs": {
            "market_score": score_value,
            "confidence": confidence,
            "regime": regime,
            "equity_position_range": range_text,
        },
    }


def risk_budget_from_market(market_payload: dict[str, Any]) -> float:
    sleeves = sleeve_targets_from_market(market_payload)
    return sum(float(sleeves[key]) for key in ("core", "mainline", "thematic"))


def sleeve_targets_from_market(market_payload: dict[str, Any]) -> dict[str, float]:
    sizing = _position_sizing_from_market(market_payload)
    active_ratio = float(sizing["final_position"]) * 100.0
    record = market_record(market_payload)
    score_value = _market_score_value(record)
    market_sleeves = _complete_market_sleeve_mix(record)
    if market_sleeves is not None:
        active = sum(float(market_sleeves[key]) for key in ("core", "mainline", "thematic"))
        if abs(active - active_ratio) > 1e-6:
            return _scale_active_sleeves(market_sleeves, active_ratio)
        return {
            "core": market_sleeves["core"],
            "mainline": market_sleeves["mainline"],
            "thematic": market_sleeves["thematic"],
            "defensive": max(0.0, 100.0 - active),
        }

    if score_value >= 70:
        core_share, mainline_share, thematic_share = 0.40, 0.45, 0.15
    elif score_value >= 55:
        core_share, mainline_share, thematic_share = 0.45, 0.45, 0.10
    elif score_value >= 40:
        core_share, mainline_share, thematic_share = 0.50, 0.4167, 0.0833
    else:
        core_share, mainline_share, thematic_share = 0.60, 0.35, 0.05

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


def allocation_policy_from_market(
    market_payload: dict[str, Any],
    sleeves: dict[str, float],
    position_sizing: dict[str, Any],
) -> dict[str, Any]:
    record = market_record(market_payload)
    range_text = _official_equity_position_range(record)
    range_bounds = parse_percent_range_bounds(range_text)
    components = position_sizing.get("components") or {}
    active = sum(float(sleeves[key]) for key in ("core", "mainline", "thematic"))
    low = range_bounds[0] if range_bounds else None
    high = range_bounds[1] if range_bounds else None
    range_violation = False
    if low is not None and high is not None:
        range_violation = active < low - 1e-6 or active > high + 1e-6
    sleeve_source = (
        "market.sleeve_mix"
        if _complete_market_sleeve_mix(record) is not None
        else "shadow_fallback_score_bands"
    )
    return {
        "position_source": components.get("source"),
        "sleeve_source": sleeve_source,
        "fallback_used": bool(components.get("fallback_used")),
        "equity_position_range": range_text,
        "equity_range_low": low,
        "equity_range_high": high,
        "target_active_weight_ratio": active,
        "range_clamped": bool(components.get("range_clamped")),
        "range_violation": range_violation,
        "raw_sleeve_mix": record.get("sleeve_mix") or {},
        "sleeve_targets": dict(sleeves),
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


def _safe_rank(signal: dict[str, Any]) -> int:
    try:
        return int(signal.get("rank") or 999)
    except (TypeError, ValueError):
        return 999


def _signal_candidates(signal: dict[str, Any]) -> list[dict[str, Any]]:
    return extract_etf_candidates(signal.get("top_etf"))


def _attach_etf_research(
    candidates: list[dict[str, Any]], etf_research_map: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for candidate in candidates:
        research = etf_research_map.get(str(candidate.get("code") or ""))
        result.append({**candidate, **({"etf_research": research} if research else {})})
    return result


def _direction_key(signal: dict[str, Any]) -> str:
    return str(signal.get("theme") or "未命名方向").strip() or "未命名方向"


def _liquidity_key(point: PricePoint | None) -> tuple[int, float, int, float]:
    if point is None:
        return (0, 0.0, 0, 0.0)
    amount = point.amount
    amount_rank = point.amount_rank
    return (
        1 if amount is not None else 0,
        float(amount or 0.0),
        1 if amount_rank is not None else 0,
        float(amount_rank or 0.0),
    )


def _direction_representative_candidate(
    candidates: list[dict[str, Any]], price_map: dict[str, PricePoint]
) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (
            *_liquidity_key(price_map.get(candidate["code"])),
            candidate["code"],
        ),
    )


def _representative_gate_report(
    *,
    signal: dict[str, Any],
    candidate: dict[str, Any],
    point: PricePoint | None,
    sleeve: str,
    representative: dict[str, Any] | None,
    reason: str,
    scoring_sleeve: str | None = None,
) -> dict[str, Any]:
    report = evaluate_etf_gate(
        signal=signal,
        candidate=candidate,
        point=point,
        sleeve=scoring_sleeve or sleeve,
    )
    report["sleeve"] = sleeve
    report["direction_key"] = _direction_key(signal)
    report["direction_rank"] = _safe_rank(signal)
    report["direction_representative_code"] = (
        representative.get("code") if representative else candidate.get("code")
    )
    report["direction_filter_pass"] = True
    report["candidate_budget_ratio"] = 0.0
    report["executed_weight_ratio"] = 0.0
    report["selected"] = False
    report["reasons"] = [*report.get("reasons", []), reason]
    return report


def _pre_allocation_gate_report(report: dict[str, Any]) -> dict[str, Any]:
    raw_execution_ratio = float(report.get("execution_ratio") or 0.0)
    tradeable = raw_execution_ratio > 0.0
    report["pre_gate_execution_ratio"] = raw_execution_ratio
    report["pre_gate_tradeable"] = tradeable
    report["gate_weight_factor"] = gate_weight_factor(report.get("grade")) if tradeable else 0.0
    if tradeable:
        report["execution_ratio"] = 1.0
        report["reasons"] = list(
            dict.fromkeys(
                [
                    *report.get("reasons", []),
                    "ETF门禁等级参与仓位权重",
                    "ETF门禁前置通过，仓位不再折扣",
                ]
            )
        )
    else:
        report["execution_ratio"] = 0.0
        report["reasons"] = list(
            dict.fromkeys([*report.get("reasons", []), "ETF门禁前置过滤"])
        )
    return report


def _stage_priority(stage: str | None) -> int:
    label = stage or ""
    if "主线确认" in label:
        return 0
    if "次主线" in label or "强修复" in label:
        return 1
    if "观察" in label:
        return 2
    if "弱势" in label or "退潮" in label:
        return 4
    return 3


def _direction_signal_sort_key(signal: dict[str, Any]) -> tuple[int, int, float, str]:
    return (
        _safe_rank(signal),
        _stage_priority(str(signal.get("stage") or "")),
        -_signal_weight(signal),
        _direction_key(signal),
    )


def _direction_gate_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_direction: dict[str, dict[str, Any]] = {}
    for signal in signals:
        if not _signal_candidates(signal):
            continue
        direction_key = _direction_key(signal)
        current = by_direction.get(direction_key)
        if current is None or _direction_signal_sort_key(signal) < _direction_signal_sort_key(current):
            by_direction[direction_key] = signal
    return sorted(by_direction.values(), key=_direction_signal_sort_key)


def _gate_display_sleeve(signal: dict[str, Any]) -> tuple[str, str]:
    stage = str(signal.get("stage") or "")
    if "主线确认" in stage or "次主线" in stage or "强修复" in stage:
        return "mainline_watch", "mainline"
    if "观察" in stage:
        return "watch", "thematic"
    return "candidate", "thematic"


def _first_candidate(signal: dict[str, Any]) -> dict[str, Any] | None:
    candidates = _signal_candidates(signal)
    return candidates[0] if candidates else None


def _return_component(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 45.0
    return clamp(((value - low) / (high - low)) * 100.0, 0.0, 100.0)


def _market_performance_score(point: PricePoint | None) -> float:
    if point is None:
        return 0.0
    r20 = _return_component(point.r20, -10.0, 25.0)
    r5 = _return_component(point.r5, -5.0, 15.0)
    r1 = _return_component(point.pct_chg, -3.0, 6.0)
    source = point.source_score if point.source_score is not None else 50.0
    return clamp(r20 * 0.40 + r5 * 0.35 + r1 * 0.15 + source * 0.10, 0.0, 100.0)


def legacy_core_price_point_from_etfs(
    price_map: dict[str, PricePoint],
) -> PricePoint | None:
    weighted = 0.0
    used_weight = 0.0
    sources: list[str] = []
    for item in CORE_ETF_BASKET:
        code = str(item["code"])
        point = price_map.get(code)
        if not point or point.pct_chg is None:
            continue
        weighted += float(point.pct_chg) * float(item["weight"])
        used_weight += float(item["weight"])
        if point.source and point.source not in sources:
            sources.append(point.source)
    if used_weight <= 0:
        return None
    return PricePoint(
        code="CORE.ASHARE",
        close=None,
        pct_chg=weighted / used_weight,
        source="legacy_core_from_etf_basket" + (":" + "+".join(sources) if sources else ""),
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
    synthetic = SYNTHETIC_INSTRUMENTS.get(code)
    return {
        "code": code,
        "display_code": synthetic["display_code"] if synthetic else code,
        "instrument_type": synthetic["instrument_type"] if synthetic else "etf",
        "is_synthetic": bool(synthetic),
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


def _core_etf_rows(
    core_weight: float, price_map: dict[str, PricePoint], market_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    if core_weight < 1.0:
        return []
    stage = market_record(market_payload).get("market_regime") or "核心底仓"
    rows: list[dict[str, Any]] = []
    for item in CORE_ETF_BASKET:
        code = str(item["code"])
        rows.append(
            _priced_row(
                code=code,
                name=str(item["name"]),
                sleeve="core",
                theme=str(item["theme"]),
                stage=stage,
                target_weight_ratio=core_weight * float(item["weight"]),
                evidence_score=None,
                point=price_map.get(code),
                source_note="core.etf_basket",
            )
        )
    return rows


def _mainline_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for signal in signals:
        stage = str(signal.get("stage") or "")
        if "主线确认" not in stage and "次主线" not in stage and "强修复" not in stage:
            continue
        if _first_candidate(signal) is None:
            continue
        result.append(signal)
    return sorted(result, key=_safe_rank)[:3]


def _thematic_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for signal in signals:
        stage = str(signal.get("stage") or "")
        try:
            evidence_score = float(signal.get("evidence_score") or 0.0)
        except (TypeError, ValueError):
            evidence_score = 0.0

        is_mainline_extension = (
            "主线确认" in stage or "次主线" in stage or "强修复" in stage
        )
        is_observation_breakout = "观察" in stage
        if not is_mainline_extension and not is_observation_breakout:
            continue
        if is_observation_breakout and evidence_score < 70:
            continue
        if is_mainline_extension and evidence_score < 75:
            continue
        if _first_candidate(signal) is None:
            continue
        candidates.append(signal)
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("evidence_score") or 0.0),
            _safe_rank(item),
        ),
    )[:8]


def _distribute_budget(
    signals: list[dict[str, Any]],
    budget: float,
    price_map: dict[str, PricePoint],
    sleeve: str,
    etf_research_map: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    if budget <= 0 or not signals:
        return [], budget, []

    entries: list[dict[str, Any]] = []
    gate_reports: list[dict[str, Any]] = []
    for signal in signals:
        candidates = _attach_etf_research(_signal_candidates(signal), etf_research_map or {})
        if not candidates:
            continue
        representative = _direction_representative_candidate(candidates, price_map)
        if representative is None:
            continue
        report = _pre_allocation_gate_report(
            _representative_gate_report(
                signal=signal,
                candidate=representative,
                point=price_map.get(representative["code"]),
                sleeve=sleeve,
                representative=representative,
                reason="同方向成交额最大ETF",
            )
        )
        report["candidate_budget_ratio"] = 0.0
        report["executed_weight_ratio"] = 0.0
        report["selected"] = False
        gate_reports.append(report)
        entries.append(
            {
                "signal": signal,
                "representative": representative,
                "gate_report": report,
                "weight": (
                    _signal_weight(signal)
                    * stage_multiplier(signal.get("stage"))
                    * float(report.get("gate_weight_factor") or 0.0)
                ),
            }
        )

    tradeable_entries = filter_tradeable_etfs(entries)
    total_weight = sum(float(entry.get("weight") or 0.0) for entry in tradeable_entries)
    if total_weight <= 0:
        return [], budget, gate_reports

    rows: list[dict[str, Any]] = []
    for entry in tradeable_entries:
        signal = entry["signal"]
        selected = entry["gate_report"]
        target_weight = budget * float(entry.get("weight") or 0.0) / total_weight
        executed_weight = target_weight
        selected["candidate_budget_ratio"] = target_weight
        selected["executed_weight_ratio"] = executed_weight
        selected["selected"] = executed_weight > 0.0
        point = price_map.get(selected["code"])
        rows.append(
            _priced_row(
                code=selected["code"],
                name=selected["name"],
                sleeve=sleeve,
                theme=signal.get("theme") or "",
                stage=signal.get("stage") or "",
                target_weight_ratio=executed_weight,
                evidence_score=signal.get("evidence_score"),
                point=point,
                source_note="theme_signal.pre_gate_filtered",
            )
        )
        rows[-1].update(
            {
                "pre_gate_weight_ratio": target_weight,
                "etf_gate_grade": selected["grade"],
                "etf_gate_score": selected["score"],
                "etf_gate_pass": selected["execution_ratio"] > 0,
                "etf_execution_ratio": selected["execution_ratio"],
                "etf_gate_reasons": selected["reasons"],
                "etf_gate_reject_reasons": selected["reject_reasons"],
                "etf_gate_data_gaps": selected["data_gaps"],
                "etf_gate_components": {
                    **(selected.get("components") or {}),
                    "gate_weight_factor": selected.get("gate_weight_factor"),
                },
            }
        )
    return rows, 0.0, gate_reports


def _distribute_thematic_budget(
    signals: list[dict[str, Any]],
    budget: float,
    price_map: dict[str, PricePoint],
    excluded_codes: set[str],
    excluded_directions: set[str],
    etf_research_map: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    if budget <= 0 or not signals:
        return [], budget, []

    reviewed: list[dict[str, Any]] = []
    for signal in signals:
        direction_key = _direction_key(signal)
        candidates = _attach_etf_research(_signal_candidates(signal), etf_research_map or {})
        representative = _direction_representative_candidate(candidates, price_map)
        if representative is None:
            continue
        for candidate in candidates:
            code = candidate["code"]
            point = price_map.get(code)
            if code != representative["code"]:
                continue
            if direction_key in excluded_directions:
                continue
            if code in excluded_codes:
                continue
            report = _pre_allocation_gate_report(
                _representative_gate_report(
                    signal=signal,
                    candidate=candidate,
                    point=point,
                    sleeve="thematic",
                    representative=representative,
                    reason="同方向成交额最大ETF",
                )
            )
            performance_score = _market_performance_score(point)
            fit_score = float((report.get("components") or {}).get("fit") or 0.0)
            priority_score = (
                performance_score * 0.55
                + float(report.get("score") or 0.0) * 0.30
                + fit_score * 0.15
            )
            priority_before_gate_factor = priority_score
            priority_score *= float(report.get("gate_weight_factor") or 0.0)
            report["market_performance_score"] = round(performance_score, 2)
            report["thematic_priority_before_gate_factor"] = round(
                priority_before_gate_factor, 2
            )
            report["thematic_priority_score"] = round(priority_score, 2)
            report["candidate_budget_ratio"] = 0.0
            report["executed_weight_ratio"] = 0.0
            report["selected"] = False
            reviewed.append(report)

    tradeable_reports = [
        entry["gate_report"]
        for entry in filter_tradeable_etfs([{"gate_report": row} for row in reviewed])
    ]
    eligible = [
        row
        for row in tradeable_reports
        if float(row.get("market_performance_score") or 0.0) >= 50.0
    ]
    if not eligible:
        for row in reviewed:
            if float(row.get("execution_ratio") or 0.0) > 0.0:
                row["reasons"] = [
                    *row.get("reasons", []),
                    "市场表现未达到主题仓位要求",
                ]
        return [], budget, reviewed

    max_rows = 2 if budget >= 5.0 else 1
    ordered_eligible = sorted(
        eligible,
        key=lambda row: (
            -float(row.get("thematic_priority_score") or 0.0),
            -float(row.get("market_performance_score") or 0.0),
            _safe_rank({"rank": row.get("direction_rank")}),
            str(row.get("code") or ""),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_code_set: set[str] = set()
    for row in ordered_eligible:
        code = str(row.get("code") or "")
        if code in selected_code_set:
            continue
        selected.append(row)
        selected_code_set.add(code)
        if len(selected) >= max_rows:
            break
    selected_reports = {id(row) for row in selected}
    priority_total = sum(float(row.get("thematic_priority_score") or 0.0) for row in selected)
    if priority_total <= 0:
        priority_total = float(len(selected))

    rows: list[dict[str, Any]] = []
    for report in reviewed:
        if id(report) not in selected_reports:
            if float(report.get("execution_ratio") or 0.0) > 0.0:
                report["reasons"] = [
                    *report.get("reasons", []),
                    "主题表现排序未入选",
                ]
            continue
        target_weight = budget * float(report.get("thematic_priority_score") or 1.0) / priority_total
        executed_weight = target_weight
        report["candidate_budget_ratio"] = target_weight
        report["executed_weight_ratio"] = executed_weight
        report["selected"] = executed_weight > 0.0

        point = price_map.get(report["code"])
        rows.append(
            _priced_row(
                code=report["code"],
                name=report["name"],
                sleeve="thematic",
                theme=report.get("theme") or "",
                stage=report.get("stage") or "",
                target_weight_ratio=executed_weight,
                evidence_score=None,
                point=point,
                source_note="theme_signal.market_performance",
            )
        )
        rows[-1].update(
            {
                "pre_gate_weight_ratio": target_weight,
                "etf_gate_grade": report["grade"],
                "etf_gate_score": report["score"],
                "etf_gate_pass": report["execution_ratio"] > 0,
                "etf_execution_ratio": report["execution_ratio"],
                "etf_gate_reasons": [
                    *report.get("reasons", []),
                    "主题仓位按市场表现优先",
                ],
                "etf_gate_reject_reasons": report["reject_reasons"],
                "etf_gate_data_gaps": report["data_gaps"],
                "etf_gate_components": {
                    **(report.get("components") or {}),
                    "gate_weight_factor": report.get("gate_weight_factor"),
                    "market_performance": report.get("market_performance_score"),
                    "thematic_priority_before_gate_factor": report.get(
                        "thematic_priority_before_gate_factor"
                    ),
                    "thematic_priority": report.get("thematic_priority_score"),
                },
            }
        )

    used = sum(float(row["target_weight_ratio"]) for row in rows)
    return rows, max(0.0, budget - used), reviewed


def _defensive_quality_target(defensive_weight: float, market_payload: dict[str, Any]) -> float:
    record = market_record(market_payload)
    score = _market_score_value(record)
    regime = _regime_value(record)
    if defensive_weight <= 0:
        return 0.0
    if regime == "risk_on" or score >= 60:
        share = 0.25
        cap = 12.0
    elif score >= 40:
        share = 0.35
        cap = 18.0
    else:
        share = 0.40
        cap = 25.0
    return min(defensive_weight * share, cap)


def _distribute_defensive_quality_budget(
    budget: float,
    market_payload: dict[str, Any],
    price_map: dict[str, PricePoint],
    etf_payload: dict[str, Any] | None,
    etf_research_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    target = _defensive_quality_target(budget, market_payload)
    if target <= 0:
        return [], budget, []

    reviewed: list[dict[str, Any]] = []
    candidates = defensive_quality_candidates(etf_payload)
    for item in candidates:
        code = str(item.get("code") or "")
        if not code:
            continue
        candidate = {
            "code": code,
            "name": item.get("name") or code,
            "etf_research": etf_research_map.get(code) or item,
        }
        signal = {
            "theme": item.get("theme") or item.get("category_key") or "收益防御",
            "stage": "收益防御",
            "evidence_score": item.get("deep_score") or 70.0,
            "score_weight_ratio": item.get("deep_score") or 70.0,
            "etf_research": candidate["etf_research"],
        }
        report = _pre_allocation_gate_report(
            _representative_gate_report(
                signal=signal,
                candidate=candidate,
                point=price_map.get(code),
                sleeve="defensive_quality",
                representative=candidate,
                reason="收益防御ETF候选",
                scoring_sleeve="defensive_quality",
            )
        )
        report["candidate_budget_ratio"] = 0.0
        report["executed_weight_ratio"] = 0.0
        report["selected"] = False
        reviewed.append(report)

    eligible = [
        row
        for row in reviewed
        if row.get("pre_gate_tradeable") and str(row.get("grade") or "") in {"A", "B", "C"}
    ]
    if not eligible:
        return [], budget, reviewed
    selected = sorted(
        eligible,
        key=lambda row: (
            -float(row.get("score") or 0.0),
            -float(((row.get("metrics") or {}).get("amount")) or 0.0),
            str(row.get("code") or ""),
        ),
    )[:2]
    total_score = sum(float(row.get("score") or 1.0) for row in selected) or float(len(selected))
    rows: list[dict[str, Any]] = []
    for report in reviewed:
        if report not in selected:
            if report.get("pre_gate_tradeable"):
                report["reasons"] = list(
                    dict.fromkeys([*report.get("reasons", []), "收益防御排序未入选"])
                )
            continue
        target_weight = target * float(report.get("score") or 1.0) / total_score
        report["candidate_budget_ratio"] = target_weight
        report["executed_weight_ratio"] = target_weight
        report["selected"] = True
        point = price_map.get(str(report["code"]))
        row = _priced_row(
            code=str(report["code"]),
            name=str(report["name"]),
            sleeve="defensive",
            theme=str(report.get("theme") or "收益防御"),
            stage="收益防御",
            target_weight_ratio=target_weight,
            evidence_score=None,
            point=point,
            source_note="defensive.quality_etf",
        )
        row.update(
            {
                "pre_gate_weight_ratio": target_weight,
                "etf_gate_grade": report["grade"],
                "etf_gate_score": report["score"],
                "etf_gate_pass": report["execution_ratio"] > 0,
                "etf_execution_ratio": report["execution_ratio"],
                "etf_gate_reasons": [
                    *report.get("reasons", []),
                    "防御仓收益层，不按主线追涨规则处理",
                ],
                "etf_gate_reject_reasons": report["reject_reasons"],
                "etf_gate_data_gaps": report["data_gaps"],
                "etf_gate_components": {
                    **(report.get("components") or {}),
                    "defensive_layer": "quality",
                    "gate_weight_factor": report.get("gate_weight_factor"),
                },
            }
        )
        rows.append(row)
    used = sum(float(row["target_weight_ratio"]) for row in rows)
    return rows, max(0.0, budget - used), reviewed


def _stock_theme_score(candidate: dict[str, Any]) -> float:
    score, _reasons, rejects = stock_gate_score(candidate)
    return 0.0 if rejects else score


def _distribute_stock_leader_budget(
    signals: list[dict[str, Any]],
    budget: float,
    price_map: dict[str, PricePoint],
    stock_payload: dict[str, Any] | None,
    excluded_directions: set[str],
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    if budget <= 0 or not stock_payload:
        return [], budget, []
    leader_signals = [
        signal
        for signal in signals
        if signal.get("instrument_preference") in {"leader", "etf_then_leader"}
        and _direction_key(signal) not in excluded_directions
    ]
    if not leader_signals:
        return [], budget, []

    reviewed: list[dict[str, Any]] = []
    for candidate in extract_stock_candidates(stock_payload):
        matched_signal = next(
            (signal for signal in leader_signals if theme_matches_stock(signal, candidate)),
            None,
        )
        if matched_signal is None:
            continue
        score, reasons, reject_reasons = stock_gate_score(candidate)
        reviewed.append(
            {
                "code": candidate["code"],
                "name": candidate["name"],
                "theme": candidate.get("theme") or _direction_key(matched_signal),
                "stage": matched_signal.get("stage") or "龙头弹性",
                "score": score,
                "grade": "A" if score >= 80 and not reject_reasons else "B" if score >= 70 and not reject_reasons else "D",
                "selected": False,
                "candidate_budget_ratio": 0.0,
                "executed_weight_ratio": 0.0,
                "reasons": reasons,
                "reject_reasons": reject_reasons,
                "data_gaps": candidate.get("data_gaps") or [],
                "candidate": candidate,
            }
        )

    eligible = [row for row in reviewed if row["grade"] in {"A", "B"}]
    if not eligible:
        return [], budget, reviewed

    stock_budget = min(budget, 8.0)
    selected = sorted(eligible, key=lambda row: (-float(row["score"]), row["code"]))[:2]
    total = sum(float(row["score"]) for row in selected) or float(len(selected))
    rows: list[dict[str, Any]] = []
    for report in reviewed:
        if report not in selected:
            if report["grade"] in {"A", "B"}:
                report["reasons"] = list(
                    dict.fromkeys([*report.get("reasons", []), "龙头弹性排序未入选"])
                )
            continue
        target_weight = min(6.0, stock_budget * float(report["score"]) / total)
        report["candidate_budget_ratio"] = target_weight
        report["executed_weight_ratio"] = target_weight
        report["selected"] = True
        point = price_map.get(str(report["code"]))
        row = _priced_row(
            code=str(report["code"]),
            name=str(report["name"]),
            sleeve="thematic",
            theme=str(report["theme"]),
            stage=str(report["stage"]),
            target_weight_ratio=target_weight,
            evidence_score=report["score"],
            point=point,
            source_note="stock_research.leader_optional",
        )
        row.update(
            {
                "instrument_type": "stock",
                "pre_gate_weight_ratio": target_weight,
                "etf_gate_grade": report["grade"],
                "etf_gate_score": report["score"],
                "etf_gate_pass": True,
                "etf_execution_ratio": 1.0,
                "etf_gate_reasons": [
                    *report.get("reasons", []),
                    "来自MyInvestStock同日龙头研究，按主题弹性小仓处理",
                ],
                "etf_gate_reject_reasons": report["reject_reasons"],
                "etf_gate_data_gaps": report["data_gaps"],
                "etf_gate_components": {
                    "instrument_gate": "stock_leader",
                    "single_name_cap_ratio": 6.0,
                },
            }
        )
        rows.append(row)
    used = sum(float(row["target_weight_ratio"]) for row in rows)
    return rows, max(0.0, budget - used), reviewed


def _actual_gate_report_by_direction(
    gate_reports: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    sleeve_priority = {"mainline": 0, "thematic": 1}
    for report in gate_reports:
        direction_key = str(report.get("direction_key") or report.get("theme") or "")
        if not direction_key:
            continue
        current = result.get(direction_key)
        if current is None:
            result[direction_key] = report
            continue
        current_key = (
            0 if current.get("selected") else 1,
            sleeve_priority.get(str(current.get("sleeve") or ""), 9),
            -float(current.get("score") or 0.0),
        )
        report_key = (
            0 if report.get("selected") else 1,
            sleeve_priority.get(str(report.get("sleeve") or ""), 9),
            -float(report.get("score") or 0.0),
        )
        if report_key < current_key:
            result[direction_key] = report
    return result


def _direction_gate_reports(
    signals: list[dict[str, Any]],
    price_map: dict[str, PricePoint],
    actual_gate_reports: list[dict[str, Any]],
    etf_research_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    actual_by_direction = _actual_gate_report_by_direction(actual_gate_reports)
    reports: list[dict[str, Any]] = []
    for signal in _direction_gate_signals(signals):
        direction_key = _direction_key(signal)
        candidates = _attach_etf_research(_signal_candidates(signal), etf_research_map or {})
        representative = _direction_representative_candidate(candidates, price_map)
        if representative is None:
            continue

        actual = actual_by_direction.get(direction_key)
        if (
            actual is not None
            and actual.get("direction_representative_code") == representative["code"]
        ):
            if not actual.get("selected") and float(actual.get("candidate_budget_ratio") or 0.0) <= 0.0:
                display_sleeve, _scoring_sleeve = _gate_display_sleeve(signal)
                actual = {
                    **actual,
                    "sleeve": display_sleeve,
                    "reasons": list(
                        dict.fromkeys(
                            [
                                *actual.get("reasons", []),
                                "方向备选，未进入本次仓位",
                            ]
                        )
                    ),
                }
                if display_sleeve == "watch":
                    actual["reasons"] = list(
                        dict.fromkeys([*actual.get("reasons", []), "观察线保留备选"])
                    )
            reports.append(actual)
            continue

        display_sleeve, scoring_sleeve = _gate_display_sleeve(signal)
        report = _representative_gate_report(
            signal=signal,
            candidate=representative,
            point=price_map.get(representative["code"]),
            sleeve=display_sleeve,
            scoring_sleeve=scoring_sleeve,
            representative=representative,
            reason="同方向成交额最大ETF",
        )
        report["reasons"] = [
            *report.get("reasons", []),
            "方向备选，未进入本次仓位",
        ]
        if display_sleeve == "watch":
            report["reasons"] = [*report["reasons"], "观察线保留备选"]
        report["reasons"] = list(dict.fromkeys(report.get("reasons", [])))
        reports.append(report)
    return reports


def _gate_summary(gate_reports: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in gate_reports if row.get("selected")]
    rejected = [row for row in gate_reports if row.get("execution_ratio") == 0.0]
    discounted = [
        row
        for row in selected
        if 0.0 < float(row.get("execution_ratio") or 0.0) < 1.0
    ]
    by_grade = {"A": 0, "B": 0, "C": 0, "D": 0}
    for row in gate_reports:
        grade = row.get("grade")
        if grade in by_grade:
            by_grade[grade] += 1
    return {
        "reviewed_count": len(gate_reports),
        "selected_count": len(selected),
        "discounted_selected_count": len(discounted),
        "rejected_count": len(rejected),
        "by_grade": by_grade,
    }


def _scale_sleeve_rows(rows: list[dict[str, Any]], sleeve: str, target_weight: float) -> None:
    sleeve_rows = [row for row in rows if row.get("sleeve") == sleeve]
    current_weight = sum(float(row.get("target_weight_ratio") or 0.0) for row in sleeve_rows)
    if not sleeve_rows or current_weight <= 0.0:
        return
    scale = target_weight / current_weight
    for row in sleeve_rows:
        row["target_weight_ratio"] = float(row.get("target_weight_ratio") or 0.0) * scale
        if row.get("pre_gate_weight_ratio") is not None:
            row["pre_gate_weight_ratio"] = float(row.get("pre_gate_weight_ratio") or 0.0) * scale
        if row.get("drift_ratio") is not None:
            row["drift_ratio"] = float(row.get("target_weight_ratio") or 0.0) - float(
                row.get("previous_weight_ratio") or 0.0
            )


def allocation_candidate_codes(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
    etf_payload: dict[str, Any] | None = None,
    stock_payload: dict[str, Any] | None = None,
) -> list[str]:
    signals = theme_payload.get("theme_signals") or []
    codes = {str(item["code"]) for item in CORE_ETF_BASKET}
    codes.add(str(DEFENSIVE_ETF["code"]))
    signals = enrich_theme_signals_with_etf_research(signals, etf_payload)
    for signal in _direction_gate_signals(signals):
        for candidate in _signal_candidates(signal):
            codes.add(candidate["code"])
    for item in defensive_quality_candidates(etf_payload):
        if item.get("code"):
            codes.add(str(item["code"]))
    for candidate in extract_stock_candidates(stock_payload):
        if candidate.get("code"):
            codes.add(str(candidate["code"]))
    return sorted(codes)


def allocation_plan(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
    price_map: dict[str, PricePoint] | None = None,
    etf_payload: dict[str, Any] | None = None,
    stock_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    price_map = price_map or {}
    etf_research_map = etf_research_by_code(etf_payload)
    position_sizing = _position_sizing_from_market(market_payload)
    sleeves = sleeve_targets_from_market(market_payload)
    allocation_policy = allocation_policy_from_market(
        market_payload, sleeves, position_sizing
    )
    signals = enrich_theme_signals_with_etf_research(theme_payload.get("theme_signals") or [], etf_payload)

    rows: list[dict[str, Any]] = []
    core_codes = {str(item["code"]) for item in CORE_ETF_BASKET}

    mainline_rows, mainline_unused, mainline_gate = _distribute_budget(
        _mainline_signals(signals),
        sleeves["mainline"],
        price_map,
        "mainline",
        etf_research_map,
    )
    stock_rows, remaining_thematic_budget, stock_gate = _distribute_stock_leader_budget(
        signals,
        sleeves["thematic"],
        price_map,
        stock_payload,
        {str(row.get("theme") or "") for row in mainline_rows if row.get("theme")},
    )
    thematic_rows, thematic_unused, thematic_gate = _distribute_thematic_budget(
        _thematic_signals(signals),
        remaining_thematic_budget,
        price_map,
        {*core_codes, *(row["code"] for row in mainline_rows), *(row["code"] for row in stock_rows)},
        {
            str(row.get("theme") or "")
            for row in [*mainline_rows, *stock_rows]
            if row.get("theme")
        },
        etf_research_map,
    )
    actual_gate_reports = [*mainline_gate, *thematic_gate]
    valid_universe_size = sum(1 for row in actual_gate_reports if row.get("pre_gate_tradeable"))
    target_active_weight = sum(float(sleeves[key]) for key in ("core", "mainline", "thematic"))
    structure_guard = enforce_structure_constraints(
        {
            "core": sleeves["core"],
            "mainline": sum(float(row["target_weight_ratio"]) for row in mainline_rows),
            "thematic": sum(float(row["target_weight_ratio"]) for row in [*stock_rows, *thematic_rows]),
            "unallocated": {
                "mainline": mainline_unused,
                "thematic": thematic_unused,
            },
            "valid_universe_size": valid_universe_size,
        },
        target_active_weight,
    )
    guarded_sleeves = structure_guard["allocation"]
    rows.extend(_core_etf_rows(guarded_sleeves["core"], price_map, market_payload))
    rows.extend(mainline_rows)
    rows.extend(stock_rows)
    rows.extend(thematic_rows)
    _scale_sleeve_rows(rows, "mainline", float(guarded_sleeves["mainline"]))
    _scale_sleeve_rows(rows, "thematic", float(guarded_sleeves["thematic"]))

    used_non_defensive = sum(float(row["target_weight_ratio"]) for row in rows)
    defensive_weight = float(guarded_sleeves["defensive"])
    defensive_quality_rows, cash_defensive_weight, defensive_quality_gate = _distribute_defensive_quality_budget(
        defensive_weight,
        market_payload,
        price_map,
        etf_payload,
        etf_research_map,
    )
    rows.extend(defensive_quality_rows)
    rows.append(
        _priced_row(
            code=str(DEFENSIVE_ETF["code"]),
            name=str(DEFENSIVE_ETF["name"]),
            sleeve="defensive",
            theme=str(DEFENSIVE_ETF["theme"]),
            stage="货币ETF/等待",
            target_weight_ratio=cash_defensive_weight,
            evidence_score=None,
            point=price_map.get(str(DEFENSIVE_ETF["code"])),
            source_note="defensive.money_market_etf",
        )
    )

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            {"core": 0, "mainline": 1, "thematic": 2, "defensive": 3}.get(
                row["sleeve"], 9
            ),
            -float(row["target_weight_ratio"]),
        ),
    )
    gate_reports = [
        *_direction_gate_reports(signals, price_map, actual_gate_reports, etf_research_map),
        *defensive_quality_gate,
    ]
    gate_universe_audit = {
        "pre_gate_universe_size": len(actual_gate_reports),
        "post_gate_universe_size": valid_universe_size,
        "valid_universe_size": valid_universe_size,
        "filtered_universe_size": sum(
            1 for row in actual_gate_reports if not row.get("pre_gate_tradeable")
        ),
        "mainline_unallocated_ratio": mainline_unused,
        "thematic_unallocated_ratio": thematic_unused,
        "stock_candidate_count": len(stock_gate),
        "stock_selected_count": sum(1 for row in stock_gate if row.get("selected")),
        "defensive_quality_candidate_count": len(defensive_quality_gate),
        "defensive_quality_selected_count": sum(
            1 for row in defensive_quality_gate if row.get("selected")
        ),
    }
    return {
        "market_risk_budget_ratio": sum(float(sleeves[key]) for key in ("core", "mainline", "thematic")),
        "risk_budget_ratio": used_non_defensive,
        "position_sizing": position_sizing,
        "allocation_policy": allocation_policy,
        "gate_universe_audit": gate_universe_audit,
        "structure_guard_report": structure_guard["structure_guard_report"],
        "sleeve_targets_before_gate": sleeves,
        "targets": sorted_rows,
        "etf_gate": gate_reports,
        "etf_gate_summary": _gate_summary(gate_reports),
        "stock_gate": stock_gate,
        "defensive_quality_gate": defensive_quality_gate,
    }


def target_allocations(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
    price_map: dict[str, PricePoint] | None = None,
    etf_payload: dict[str, Any] | None = None,
    stock_payload: dict[str, Any] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    plan = allocation_plan(market_payload, theme_payload, price_map, etf_payload, stock_payload)
    return float(plan["risk_budget_ratio"]), plan["targets"]


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
