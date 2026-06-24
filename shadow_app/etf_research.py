from __future__ import annotations

from typing import Any

DEFENSIVE_QUALITY_ETFS: tuple[dict[str, str], ...] = (
    {
        "code": "512890.SH",
        "name": "华泰柏瑞中证红利低波动ETF",
        "theme": "红利低波",
        "category_key": "红利低波",
    },
    {
        "code": "159201.SZ",
        "name": "华夏国证自由现金流ETF",
        "theme": "自由现金流",
        "category_key": "自由现金流",
    },
    {
        "code": "159399.SZ",
        "name": "国泰富时中国A股自由现金流聚焦ETF",
        "theme": "自由现金流",
        "category_key": "自由现金流",
    },
)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def etf_basis_date(payload: dict[str, Any]) -> str | None:
    report = payload.get("report") or {}
    result = payload.get("result") or {}
    return (
        payload.get("basis_date")
        or report.get("basis_date")
        or result.get("basis_date")
    )


def _normalize_item(raw: dict[str, Any], wrapper: dict[str, Any] | None = None) -> dict[str, Any]:
    wrapper = wrapper or {}
    latest = ((wrapper.get("research") or {}).get("latest") or {})
    valuation_signal = latest.get("valuation_signal") or {}
    market = raw.get("market") or {}
    scores = raw.get("scores") or {}
    return {
        "code": raw.get("code"),
        "name": raw.get("name") or raw.get("code"),
        "valuation_model_type": raw.get("valuation_model_type")
        or latest.get("valuation_model_type")
        or valuation_signal.get("valuation_model_type"),
        "sleeve_key": raw.get("sleeve_key")
        or latest.get("sleeve_key")
        or valuation_signal.get("sleeve_key"),
        "category_key": raw.get("category_key") or raw.get("theme"),
        "theme": raw.get("theme"),
        "themes": raw.get("themes") or [],
        "deep_rating": raw.get("deep_rating"),
        "deep_score": _safe_float(raw.get("deep_score")),
        "shadow_observation_eligible": bool(raw.get("shadow_observation_eligible", True)),
        "market": market,
        "scores": scores,
        "upstream_signal": raw.get("upstream_signal") or {},
        "decision_matrix": wrapper.get("decision_matrix") or {},
        "valuation_signal": valuation_signal,
        "risk_flags": raw.get("risk_flags") or [],
        "data_gaps": list(dict.fromkeys([*(raw.get("data_gaps") or []), *(latest.get("data_gaps") or [])])),
        "links": raw.get("links") or {},
        "source": "MyInvestETF",
    }


def extract_etf_research_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    items: list[dict[str, Any]] = []
    for wrapper in payload.get("etfs") or []:
        leader = wrapper.get("leader")
        if isinstance(leader, dict):
            items.append(_normalize_item(leader, wrapper))
    for raw in ((payload.get("key_results") or {}).get("primary_output") or {}).get("items") or []:
        if isinstance(raw, dict):
            items.append(_normalize_item(raw))
    for raw in payload.get("items") or []:
        if isinstance(raw, dict):
            items.append(_normalize_item(raw))

    by_code: dict[str, dict[str, Any]] = {}
    for item in items:
        code = str(item.get("code") or "")
        if not code:
            continue
        current = by_code.get(code)
        if current is None:
            by_code[code] = item
            continue
        if (item.get("valuation_signal") or {}) and not (current.get("valuation_signal") or {}):
            by_code[code] = item
    return list(by_code.values())


def etf_research_by_code(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {str(item["code"]): item for item in extract_etf_research_items(payload)}


def _text_tokens(text: str) -> set[str]:
    tokens = set()
    for token in (
        "半导体",
        "芯片",
        "消费电子",
        "通信",
        "算力",
        "机器人",
        "军工",
        "机床",
        "电力",
        "新能源",
        "资源",
        "稀土",
        "医药",
        "创新药",
        "红利",
        "低波",
        "自由现金流",
    ):
        if token in text:
            tokens.add(token)
    return tokens


def theme_matches_etf(signal: dict[str, Any], item: dict[str, Any]) -> bool:
    signal_text = " ".join(
        str(signal.get(key) or "") for key in ("theme", "theme_id", "top_etf")
    )
    item_text = " ".join(
        str(item.get(key) or "")
        for key in ("name", "theme", "category_key", "valuation_model_type", "sleeve_key")
    )
    return bool(_text_tokens(signal_text) & _text_tokens(item_text))


def enrich_theme_signals_with_etf_research(
    signals: list[dict[str, Any]], etf_payload: dict[str, Any] | None
) -> list[dict[str, Any]]:
    items = extract_etf_research_items(etf_payload)
    if not items:
        return signals
    result: list[dict[str, Any]] = []
    for signal in signals:
        top_etf = str(signal.get("top_etf") or "")
        additions: list[str] = []
        for item in items:
            code = str(item.get("code") or "")
            if not code or code in top_etf:
                continue
            if theme_matches_etf(signal, item):
                additions.append(f"{code} {item.get('name') or code}")
        if additions:
            signal = {**signal, "top_etf": "、".join([top_etf, *additions]).strip("、")}
        result.append(signal)
    return result


def etf_research_price_fallback(payload: dict[str, Any] | None) -> dict[str, Any]:
    from .pricing import PricePoint

    result: dict[str, PricePoint] = {}
    for item in extract_etf_research_items(payload):
        code = str(item.get("code") or "")
        if not code:
            continue
        market = item.get("market") or {}
        scores = item.get("scores") or {}
        result[code] = PricePoint(
            code=code,
            close=_safe_float(market.get("close")),
            pct_chg=_safe_float(market.get("r1") or market.get("pct_chg")),
            source="MyInvestETF",
            amount=_safe_float(market.get("amount")),
            r5=_safe_float(market.get("r5")),
            r20=_safe_float(market.get("r20")),
            source_score=_safe_float(item.get("deep_score")),
            amount_rank=_safe_float(scores.get("amount_rank")),
            r1_rank=_safe_float(scores.get("r1_rank")),
            r5_rank=_safe_float(scores.get("r5_rank")),
            r20_rank=_safe_float(scores.get("r20_rank")),
        )
    return result


def defensive_quality_candidates(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in extract_etf_research_items(payload):
        text = " ".join(
            str(item.get(key) or "")
            for key in ("name", "valuation_model_type", "sleeve_key", "category_key")
        )
        if (
            item.get("sleeve_key") == "defensive_quality"
            or item.get("valuation_model_type") == "factor_defensive"
            or any(token in text for token in ("红利", "低波", "自由现金流", "现金流"))
        ):
            result.append(item)
    known = {str(item.get("code")) for item in result}
    for item in DEFENSIVE_QUALITY_ETFS:
        if item["code"] not in known:
            result.append(
                {
                    **item,
                    "valuation_model_type": "factor_defensive",
                    "sleeve_key": "defensive_quality",
                    "deep_rating": None,
                    "deep_score": None,
                    "shadow_observation_eligible": True,
                    "market": {},
                    "scores": {},
                    "risk_flags": [],
                    "data_gaps": ["MyInvestETF 尚未提供该防御ETF的完整研究输出"],
                    "source": "shadow.defensive_quality_defaults",
                }
            )
    return result
