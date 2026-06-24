from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stock_basis_date(payload: dict[str, Any]) -> str | None:
    report = payload.get("report") or {}
    result = payload.get("result") or {}
    return payload.get("basis_date") or report.get("basis_date") or result.get("basis_date")


def extract_stock_candidates(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw_items = [
        *(((payload.get("key_results") or {}).get("primary_output") or {}).get("items") or []),
        *(payload.get("stocks") or []),
    ]
    result: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        wrapper = raw
        raw = raw.get("leader") if isinstance(raw.get("leader"), dict) else raw
        code = raw.get("code")
        if not code:
            continue
        latest = ((wrapper.get("research") or {}).get("latest") or {})
        result.append(
            {
                "code": str(code),
                "name": raw.get("name") or latest.get("name") or code,
                "theme": raw.get("theme") or "",
                "themes": raw.get("themes") or [],
                "deep_rating": raw.get("deep_rating"),
                "deep_score": _safe_float(raw.get("deep_score")),
                "shadow_observation_eligible": bool(
                    raw.get("shadow_observation_eligible", True)
                ),
                "candidate": raw.get("candidate") or {},
                "market": raw.get("market") or {},
                "scores": raw.get("scores") or {},
                "theme_context": raw.get("theme_context") or {},
                "upstream_signal": raw.get("upstream_signal") or {},
                "risk_flags": raw.get("risk_flags") or [],
                "data_gaps": list(
                    dict.fromkeys([*(raw.get("data_gaps") or []), *(latest.get("data_gaps") or [])])
                ),
                "links": raw.get("links") or {},
                "source": "MyInvestStock",
            }
        )
    return result


def theme_matches_stock(signal: dict[str, Any], candidate: dict[str, Any]) -> bool:
    signal_text = str(signal.get("theme") or "")
    candidate_text = " ".join(
        [str(candidate.get("theme") or ""), *[str(item) for item in candidate.get("themes") or []]]
    )
    if signal_text and signal_text in candidate_text:
        return True
    if candidate_text and candidate_text in signal_text:
        return True
    tokens = ("半导体", "芯片", "通信", "算力", "机器人", "军工", "新能源", "资源", "医药", "创新药")
    return any(token in signal_text and token in candidate_text for token in tokens)


def stock_gate_score(candidate: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    reject_reasons: list[str] = []
    scores = candidate.get("scores") or {}
    market = candidate.get("market") or {}
    candidate_info = candidate.get("candidate") or {}
    deep_score = _safe_float(candidate.get("deep_score")) or 0.0
    theme_binding = _safe_float(scores.get("theme_binding")) or 0.0
    evidence_quality = (
        _safe_float(scores.get("evidence_quality"))
        or _safe_float(candidate_info.get("evidence_score"))
        or 0.0
    )
    trading_structure = _safe_float(scores.get("trading_structure")) or 0.0
    financial_quality = _safe_float(scores.get("financial_quality")) or 50.0
    valuation_safety = _safe_float(scores.get("valuation_safety")) or 50.0
    flow = _safe_float(market.get("turnover_rate")) or 0.0
    score = (
        deep_score * 0.25
        + theme_binding * 0.25
        + evidence_quality * 0.20
        + trading_structure * 0.15
        + financial_quality * 0.10
        + valuation_safety * 0.05
    )
    if candidate.get("deep_rating") != "A":
        reject_reasons.append("个股深研未达到A档")
    if not candidate.get("shadow_observation_eligible", False):
        reject_reasons.append("上游未标记为影子可观察")
    if candidate.get("risk_flags"):
        reject_reasons.append("存在上游风险标记")
    if theme_binding < 80:
        reject_reasons.append("主线绑定不足")
    if evidence_quality < 80:
        reject_reasons.append("龙头证据不足")
    if trading_structure < 45:
        reject_reasons.append("交易结构不足")
    if flow <= 0:
        reject_reasons.append("缺少换手率或流动性数据")
    if score >= 75:
        reasons.append("龙头候选质量达到弹性仓要求")
    if trading_structure >= 55:
        reasons.append("交易结构可承接小仓")
    return round(score, 2), reasons, reject_reasons
