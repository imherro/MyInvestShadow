from __future__ import annotations

import re
from typing import Any

from .etf_gate import evaluate_etf_gate
from .pricing import PricePoint

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


def _signal_candidates(signal: dict[str, Any]) -> list[dict[str, str]]:
    return extract_etf_candidates(signal.get("top_etf"))


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
    candidates: list[dict[str, str]], price_map: dict[str, PricePoint]
) -> dict[str, str] | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (
            *_liquidity_key(price_map.get(candidate["code"])),
            candidate["code"],
        ),
    )


def _direction_gate_report(
    *,
    signal: dict[str, Any],
    candidate: dict[str, str],
    point: PricePoint | None,
    sleeve: str,
    representative: dict[str, str] | None,
    passed: bool,
    reason: str,
) -> dict[str, Any]:
    report = evaluate_etf_gate(
        signal=signal,
        candidate=candidate,
        point=point,
        sleeve=sleeve,
    )
    report["direction_key"] = _direction_key(signal)
    report["direction_representative_code"] = (
        representative.get("code") if representative else candidate.get("code")
    )
    report["direction_filter_pass"] = passed
    report["candidate_budget_ratio"] = 0.0
    report["executed_weight_ratio"] = 0.0
    report["selected"] = False
    if passed:
        report["reasons"] = [*report.get("reasons", []), reason]
        return report

    report["grade"] = "D"
    report["hard_pass"] = False
    report["execution_ratio"] = 0.0
    report["reasons"] = [*report.get("reasons", []), reason]
    report["reject_reasons"] = [*report.get("reject_reasons", []), reason]
    return report


def _first_candidate(signal: dict[str, Any]) -> dict[str, str] | None:
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
    return sorted(result, key=lambda item: int(item.get("rank") or 999))[:3]


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
            int(item.get("rank") or 999),
        ),
    )[:8]


def _distribute_budget(
    signals: list[dict[str, Any]], budget: float, price_map: dict[str, PricePoint], sleeve: str
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    if budget <= 0 or not signals:
        return [], budget, []
    weights = [_signal_weight(signal) * stage_multiplier(signal.get("stage")) for signal in signals]
    total_weight = sum(weights)
    if total_weight <= 0:
        return [], budget, []

    rows: list[dict[str, Any]] = []
    unused = 0.0
    gate_reports: list[dict[str, Any]] = []
    for signal, weight in zip(signals, weights, strict=False):
        candidates = _signal_candidates(signal)
        if not candidates:
            continue
        representative = _direction_representative_candidate(candidates, price_map)
        if representative is None:
            continue
        target_weight = budget * weight / total_weight
        if target_weight < 1.0:
            unused += target_weight
            continue
        evaluated: list[dict[str, Any]] = []
        for candidate in candidates:
            point = price_map.get(candidate["code"])
            if candidate["code"] != representative["code"]:
                evaluated.append(
                    _direction_gate_report(
                        signal=signal,
                        candidate=candidate,
                        point=point,
                        sleeve=sleeve,
                        representative=representative,
                        passed=False,
                        reason="同方向非成交额最大ETF，不参与落地",
                    )
                )
                continue
            evaluated.append(
                _direction_gate_report(
                    signal=signal,
                    candidate=candidate,
                    point=point,
                    sleeve=sleeve,
                    representative=representative,
                    passed=True,
                    reason="同方向成交额最大ETF",
                )
            )
        selected = next(row for row in evaluated if row["code"] == representative["code"])
        executed_weight = target_weight * float(selected["execution_ratio"])
        unused += max(0.0, target_weight - executed_weight)
        for report in evaluated:
            report["candidate_budget_ratio"] = target_weight
            report["executed_weight_ratio"] = executed_weight if report["code"] == selected["code"] else 0.0
            report["selected"] = report["code"] == selected["code"] and executed_weight >= 1.0
            if report["code"] != selected["code"] and report["execution_ratio"] > 0:
                report["reasons"] = [
                    *report.get("reasons", []),
                    "评分低于入选ETF",
                ]
            gate_reports.append(report)
        if executed_weight < 1.0:
            continue
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
                source_note="theme_signal.top_etf",
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
                "etf_gate_components": selected["components"],
            }
        )
    return rows, unused, gate_reports


def _distribute_thematic_budget(
    signals: list[dict[str, Any]],
    budget: float,
    price_map: dict[str, PricePoint],
    excluded_codes: set[str],
    excluded_directions: set[str],
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    if budget <= 0 or not signals:
        return [], budget, []

    reviewed: list[dict[str, Any]] = []
    for signal in signals:
        direction_key = _direction_key(signal)
        candidates = _signal_candidates(signal)
        representative = _direction_representative_candidate(candidates, price_map)
        if representative is None:
            continue
        for candidate in candidates:
            code = candidate["code"]
            point = price_map.get(code)
            if code != representative["code"]:
                reviewed.append(
                    _direction_gate_report(
                        signal=signal,
                        candidate=candidate,
                        point=point,
                        sleeve="thematic",
                        representative=representative,
                        passed=False,
                        reason="同方向非成交额最大ETF，不参与落地",
                    )
                )
                continue
            if direction_key in excluded_directions:
                reviewed.append(
                    _direction_gate_report(
                        signal=signal,
                        candidate=candidate,
                        point=point,
                        sleeve="thematic",
                        representative=representative,
                        passed=False,
                        reason="同方向已在主线仓位持有",
                    )
                )
                continue
            if code in excluded_codes:
                reviewed.append(
                    _direction_gate_report(
                        signal=signal,
                        candidate=candidate,
                        point=point,
                        sleeve="thematic",
                        representative=representative,
                        passed=False,
                        reason="ETF已在其他仓位持有",
                    )
                )
                continue
            report = _direction_gate_report(
                signal=signal,
                candidate=candidate,
                point=point,
                sleeve="thematic",
                representative=representative,
                passed=True,
                reason="同方向成交额最大ETF",
            )
            performance_score = _market_performance_score(point)
            fit_score = float((report.get("components") or {}).get("fit") or 0.0)
            priority_score = (
                performance_score * 0.55
                + float(report.get("score") or 0.0) * 0.30
                + fit_score * 0.15
            )
            report["market_performance_score"] = round(performance_score, 2)
            report["thematic_priority_score"] = round(priority_score, 2)
            report["candidate_budget_ratio"] = 0.0
            report["executed_weight_ratio"] = 0.0
            report["selected"] = False
            reviewed.append(report)

    eligible = [
        row
        for row in reviewed
        if float(row.get("execution_ratio") or 0.0) > 0.0
        and float(row.get("market_performance_score") or 0.0) >= 50.0
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
            int(next(
                (
                    signal.get("rank") or 999
                    for signal in signals
                    if signal.get("theme") == row.get("theme")
                ),
                999,
            )),
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
    unused = 0.0
    for report in reviewed:
        if id(report) not in selected_reports:
            if float(report.get("execution_ratio") or 0.0) > 0.0:
                report["reasons"] = [
                    *report.get("reasons", []),
                    "主题表现排序未入选",
                ]
            continue
        target_weight = budget * float(report.get("thematic_priority_score") or 1.0) / priority_total
        executed_weight = target_weight * float(report.get("execution_ratio") or 0.0)
        report["candidate_budget_ratio"] = target_weight
        report["executed_weight_ratio"] = executed_weight
        report["selected"] = executed_weight >= 1.0
        unused += max(0.0, target_weight - executed_weight)
        if executed_weight < 1.0:
            continue

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
                    "market_performance": report.get("market_performance_score"),
                    "thematic_priority": report.get("thematic_priority_score"),
                },
            }
        )

    used = sum(float(row["target_weight_ratio"]) for row in rows)
    unused += max(0.0, budget - used - unused)
    return rows, unused, reviewed


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


def allocation_candidate_codes(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
) -> list[str]:
    signals = theme_payload.get("theme_signals") or []
    codes = {str(item["code"]) for item in CORE_ETF_BASKET}
    codes.add(str(DEFENSIVE_ETF["code"]))
    for signal in [*_mainline_signals(signals), *_thematic_signals(signals)]:
        for candidate in _signal_candidates(signal):
            codes.add(candidate["code"])
    return sorted(codes)


def allocation_plan(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
    price_map: dict[str, PricePoint] | None = None,
) -> dict[str, Any]:
    price_map = price_map or {}
    sleeves = sleeve_targets_from_market(market_payload)
    signals = theme_payload.get("theme_signals") or []

    rows: list[dict[str, Any]] = []
    rows.extend(_core_etf_rows(sleeves["core"], price_map, market_payload))

    mainline_rows, _mainline_unused, mainline_gate = _distribute_budget(
        _mainline_signals(signals), sleeves["mainline"], price_map, "mainline"
    )
    thematic_rows, _thematic_unused, thematic_gate = _distribute_thematic_budget(
        _thematic_signals(signals),
        sleeves["thematic"],
        price_map,
        {row["code"] for row in [*rows, *mainline_rows]},
        {str(row.get("theme") or "") for row in mainline_rows if row.get("theme")},
    )
    rows.extend(mainline_rows)
    rows.extend(thematic_rows)

    used_non_defensive = sum(float(row["target_weight_ratio"]) for row in rows)
    defensive_weight = max(0.0, 100.0 - used_non_defensive)
    rows.append(
        _priced_row(
            code=str(DEFENSIVE_ETF["code"]),
            name=str(DEFENSIVE_ETF["name"]),
            sleeve="defensive",
            theme=str(DEFENSIVE_ETF["theme"]),
            stage="货币ETF/等待",
            target_weight_ratio=defensive_weight,
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
    gate_reports = [*mainline_gate, *thematic_gate]
    return {
        "market_risk_budget_ratio": sum(float(sleeves[key]) for key in ("core", "mainline", "thematic")),
        "risk_budget_ratio": used_non_defensive,
        "sleeve_targets_before_gate": sleeves,
        "targets": sorted_rows,
        "etf_gate": gate_reports,
        "etf_gate_summary": _gate_summary(gate_reports),
    }


def target_allocations(
    market_payload: dict[str, Any],
    theme_payload: dict[str, Any],
    price_map: dict[str, PricePoint] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    plan = allocation_plan(market_payload, theme_payload, price_map)
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
