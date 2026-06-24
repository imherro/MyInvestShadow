from __future__ import annotations

from typing import Any

from .pricing import PricePoint

GRADE_WEIGHT_FACTORS = {
    "A": 1.0,
    "B": 0.85,
    "C": 0.60,
    "D": 0.0,
}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _research_payload(signal: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    raw = candidate.get("etf_research") or signal.get("etf_research") or {}
    return raw if isinstance(raw, dict) else {}


def _research_score(research: dict[str, Any], *keys: str) -> float | None:
    values: list[float] = []
    for container_key in ("valuation_signal", "scores"):
        container = research.get(container_key) or {}
        for key in keys:
            value = _safe_float(container.get(key))
            if value is not None:
                values.append(value)
    for key in keys:
        value = _safe_float(research.get(key))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return sum(values) / len(values)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _stage_score(stage: str) -> float:
    if "主线确认" in stage:
        return 100.0
    if "次主线" in stage or "强修复" in stage:
        return 88.0
    if "观察" in stage:
        return 72.0
    if "弱势" in stage or "退潮" in stage:
        return 0.0
    return 55.0


def _signal_fit_score(signal: dict[str, Any], research: dict[str, Any] | None = None) -> float:
    evidence = _safe_float(signal.get("evidence_score")) or _safe_float(
        signal.get("score_weight_ratio")
    )
    evidence = evidence if evidence is not None else 50.0
    stage = _stage_score(str(signal.get("stage") or ""))
    etf_score = _safe_float(signal.get("etf_score"))
    base = _clamp(evidence * 0.65 + stage * 0.35) if etf_score is None else _clamp(
        evidence * 0.50 + stage * 0.30 + etf_score * 0.20
    )
    research = research or {}
    research_fit = _research_score(
        research, "mainline_validity_score", "theme_binding", "mainline_strength"
    )
    deep_score = _safe_float(research.get("deep_score"))
    if research_fit is not None:
        base = base * 0.65 + research_fit * 0.25 + (deep_score or research_fit) * 0.10
    return _clamp(base)


def _rank_to_score(value: float | None) -> float | None:
    if value is None:
        return None
    if 0.0 <= value <= 1.0:
        return value * 100.0
    return _clamp(value)


def _liquidity_score(point: PricePoint | None, data_gaps: list[str]) -> tuple[float, list[str]]:
    reject_reasons: list[str] = []
    if point is None:
        data_gaps.append("缺少ETF行情与排名数据")
        reject_reasons.append("缺少可验证交易数据")
        return 0.0, reject_reasons

    rank_score = _rank_to_score(point.amount_rank)
    if rank_score is not None:
        score = rank_score
    elif point.amount is None:
        data_gaps.append("缺少成交额数据")
        score = 55.0
    elif point.amount >= 1_000_000:
        score = 100.0
    elif point.amount >= 300_000:
        score = 85.0
    elif point.amount >= 100_000:
        score = 70.0
    elif point.amount >= 30_000:
        score = 50.0
    else:
        score = 20.0

    if score < 30.0:
        reject_reasons.append("成交活跃度过低")
    return _clamp(score), reject_reasons


def _valuation_score(
    point: PricePoint | None, data_gaps: list[str], research: dict[str, Any] | None = None
) -> tuple[float, list[str]]:
    reject_reasons: list[str] = []
    research = research or {}
    research_valuation = _research_score(research, "valuation_tolerance_score", "undervalued_score")
    if research and research_valuation is None:
        data_gaps.append("MyInvestETF估值未刷新")
    if point is None:
        data_gaps.append("缺少ETF估值替代指标")
        return (research_valuation or 0.0), ["缺少估值门禁数据"] if research_valuation is None else []

    premium = point.premium_rate
    if premium is None:
        data_gaps.append("缺少净值溢价数据")
        score = 65.0
    else:
        abs_premium = abs(premium)
        if abs_premium <= 0.25:
            score = 100.0
        elif abs_premium <= 0.60:
            score = 90.0
        elif abs_premium <= 1.00:
            score = 75.0
        elif abs_premium <= 2.00:
            score = 50.0
        elif abs_premium <= 3.00:
            score = 25.0
        else:
            score = 0.0
            reject_reasons.append("溢价/折价异常")

    r1 = point.pct_chg
    r5 = point.r5
    r20 = point.r20
    if r5 is None:
        data_gaps.append("缺少5日涨幅")
    elif r5 >= 25.0:
        score -= 30.0
    elif r5 >= 18.0:
        score -= 20.0
    elif r5 <= -12.0:
        score -= 10.0

    if r20 is None:
        data_gaps.append("缺少20日涨幅")
    elif r20 >= 45.0:
        score -= 25.0
    elif r20 >= 30.0:
        score -= 15.0
    elif r20 <= -18.0:
        score -= 15.0

    if r1 is not None and r1 >= 8.0:
        score -= 8.0

    if research_valuation is not None:
        score = score * 0.55 + research_valuation * 0.45
    return _clamp(score), reject_reasons


def _trend_score(point: PricePoint | None, data_gaps: list[str]) -> float:
    if point is None:
        data_gaps.append("缺少趋势确认数据")
        return 0.0

    r5 = point.r5
    r20 = point.r20
    r1 = point.pct_chg
    if r5 is None and r20 is None and r1 is None:
        data_gaps.append("缺少1/5/20日表现数据")
        return 45.0

    score = 55.0
    if r20 is not None and r20 > 0:
        score += 18.0
    if r5 is not None and r5 > 0:
        score += 12.0
    if r1 is not None and r1 > 0:
        score += 5.0
    if r20 is not None and r20 < -8.0:
        score -= 25.0
    if r5 is not None and r5 < -5.0:
        score -= 15.0
    if r5 is not None and r5 >= 22.0:
        score = min(score, 65.0)
    return _clamp(score)


def _defensive_fit_score(candidate: dict[str, Any], research: dict[str, Any]) -> float:
    text = " ".join(
        str(value or "")
        for value in (
            candidate.get("name"),
            research.get("name"),
            research.get("valuation_model_type"),
            research.get("sleeve_key"),
            research.get("category_key"),
        )
    )
    if research.get("sleeve_key") == "defensive_quality" or research.get("valuation_model_type") == "factor_defensive":
        return 95.0
    if any(token in text for token in ("红利", "低波", "自由现金流", "现金流", "高股息")):
        return 85.0
    return 45.0


def _grade(score: float, hard_pass: bool) -> tuple[str, float]:
    if not hard_pass or score < 55.0:
        return "D", 0.0
    if score >= 80.0:
        return "A", 1.0
    if score >= 70.0:
        return "B", 0.75
    return "C", 0.45


def gate_weight_factor(grade: str | None) -> float:
    return GRADE_WEIGHT_FACTORS.get(str(grade or "").upper(), 0.0)


def evaluate_etf_gate(
    *,
    signal: dict[str, Any],
    candidate: dict[str, str],
    point: PricePoint | None,
    sleeve: str,
) -> dict[str, Any]:
    data_gaps: list[str] = []
    reject_reasons: list[str] = []
    reasons: list[str] = []
    research = _research_payload(signal, candidate)

    fit_score = _signal_fit_score(signal, research)
    liquidity_score, liquidity_rejects = _liquidity_score(point, data_gaps)
    research_liquidity = _research_score(research, "liquidity_score", "trading_structure")
    if research_liquidity is not None:
        liquidity_score = _clamp(liquidity_score * 0.65 + research_liquidity * 0.35)
    valuation_score, valuation_rejects = _valuation_score(point, data_gaps, research)
    trend_score = _trend_score(point, data_gaps)
    tracking_score = _research_score(research, "tracking_score")
    role_score = _research_score(research, "portfolio_role_score", "risk_adjusted_score")
    factor_score = _research_score(research, "factor_premium_score", "risk_adjusted_score")
    reject_reasons.extend(liquidity_rejects)
    reject_reasons.extend(valuation_rejects)
    if research and research.get("shadow_observation_eligible") is False:
        reject_reasons.append("MyInvestETF未标记为影子可观察")
    if research and research.get("deep_rating") == "D":
        reject_reasons.append("MyInvestETF深研等级为D")
    if research.get("risk_flags"):
        reject_reasons.append("MyInvestETF存在风险标记")

    if fit_score < 45.0:
        reject_reasons.append("主线匹配度不足")
    if point and point.close is None and point.pct_chg is None and point.source_score is None:
        reject_reasons.append("缺少收盘表现与ETF排名")

    data_quality_score = _clamp(100.0 - len(set(data_gaps)) * 12.0, 40.0, 100.0)
    if sleeve == "defensive_quality":
        fit_score = _defensive_fit_score(candidate, research)
        score = (
            fit_score * 0.25
            + (factor_score if factor_score is not None else valuation_score) * 0.30
            + liquidity_score * 0.20
            + (tracking_score if tracking_score is not None else data_quality_score) * 0.15
            + (role_score if role_score is not None else data_quality_score) * 0.10
        )
        if point is None:
            reject_reasons.append("缺少收益防御ETF交易数据")
    elif sleeve == "mainline":
        score = (
            fit_score * 0.35
            + trend_score * 0.25
            + liquidity_score * 0.20
            + valuation_score * 0.15
            + data_quality_score * 0.05
        )
    else:
        score = (
            fit_score * 0.30
            + trend_score * 0.25
            + liquidity_score * 0.20
            + valuation_score * 0.15
            + data_quality_score * 0.10
        )
    if sleeve == "thematic":
        score -= 5.0

    hard_pass = not reject_reasons
    score = _clamp(score)
    grade, execution_ratio = _grade(score, hard_pass)

    if fit_score >= 80.0:
        reasons.append("主线匹配度高")
    elif fit_score >= 65.0:
        reasons.append("主线匹配度中等")
    if liquidity_score >= 80.0:
        reasons.append("成交活跃度充足")
    elif liquidity_score >= 50.0:
        reasons.append("成交活跃度一般")
    if valuation_score < 55.0:
        reasons.append("估值/拥挤度偏高")
    elif valuation_score >= 75.0:
        reasons.append("溢价和拥挤度可接受")
    if point and point.r5 is not None and point.r5 >= 18.0:
        reasons.append("短期涨幅偏热，估值项降权")
    if data_gaps:
        reasons.append("存在数据缺口，估值项降权")
    if grade == "D":
        reasons.append("未通过ETF门禁")
    if research:
        reasons.append("已参考MyInvestETF研究输出")

    return {
        "code": candidate["code"],
        "name": candidate.get("name") or candidate["code"],
        "sleeve": sleeve,
        "theme": signal.get("theme") or "",
        "stage": signal.get("stage") or "",
        "score": round(score, 2),
        "grade": grade,
        "hard_pass": hard_pass,
        "gate_weight_factor": gate_weight_factor(grade),
        "execution_ratio": execution_ratio,
        "reasons": list(dict.fromkeys(reasons)),
        "reject_reasons": list(dict.fromkeys(reject_reasons)),
        "data_gaps": list(dict.fromkeys(data_gaps)),
        "components": {
            "fit": round(fit_score, 2),
            "valuation": round(valuation_score, 2),
            "liquidity": round(liquidity_score, 2),
            "trend": round(trend_score, 2),
            "data_quality": round(data_quality_score, 2),
            "tracking": round(tracking_score, 2) if tracking_score is not None else None,
            "portfolio_role": round(role_score, 2) if role_score is not None else None,
            "factor_premium": round(factor_score, 2) if factor_score is not None else None,
        },
        "research_source": research.get("source") if research else None,
        "research_deep_rating": research.get("deep_rating") if research else None,
        "research_sleeve_key": research.get("sleeve_key") if research else None,
        "research_valuation_model_type": research.get("valuation_model_type") if research else None,
        "metrics": {
            "pct_chg": point.pct_chg if point else None,
            "r5": point.r5 if point else None,
            "r20": point.r20 if point else None,
            "amount": point.amount if point else None,
            "amount_rank": point.amount_rank if point else None,
            "premium_rate": point.premium_rate if point else None,
            "source_score": point.source_score if point else None,
            "source": point.source if point else "missing",
        },
    }
