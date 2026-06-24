from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .config import (
    ETF_API_URL,
    HTTP_HEADERS,
    LEADER_API_URL,
    MARKET_API_URL,
    STOCK_API_URL,
    THEME_API_URL,
)
from .db import dumps
from .etf_research import etf_basis_date
from .phase import classify_shadow_phase, stage_from_cycle
from .stock_research import stock_basis_date


@dataclass(frozen=True)
class SourceSnapshot:
    source: str
    fetched_at: str
    basis_date: str | None
    schema_version: str | None
    content_hash: str
    ok: bool
    payload: dict[str, Any]
    error: str | None = None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def content_hash(payload: Any) -> str:
    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()


def fetch_json(url: str) -> dict[str, Any]:
    with httpx.Client(headers=HTTP_HEADERS, timeout=25.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def normalize_theme_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("theme_signals"):
        return payload
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    has_mainline_ranking = bool(result.get("mainline_ranking"))
    ranking = result.get("mainline_ranking") or result.get("theme_ranking") or []
    legacy_rows = [
        row
        for row in [*(result.get("theme_ranking") or []), *(result.get("legacy_theme_ranking") or [])]
        if isinstance(row, dict)
    ]
    legacy_by_id = {
        str(row.get("theme_id") or row.get("theme") or row.get("theme_name") or ""): row
        for row in legacy_rows
    }
    legacy_by_name = {
        str(row.get("theme") or row.get("theme_name") or ""): row for row in legacy_rows
    }
    theme_signals: list[dict[str, Any]] = []
    for index, row in enumerate(ranking, start=1):
        theme_name = row.get("theme_name") or row.get("theme") or ""
        theme_id = row.get("theme_id") or theme_name
        legacy = legacy_by_id.get(str(theme_id)) or legacy_by_name.get(str(theme_name)) or {}
        fallback_stage = str(legacy.get("stage") or row.get("stage") or "")
        stage_source = {**legacy, **row}
        stage = stage_from_cycle(stage_source, fallback_stage) if has_mainline_ranking else fallback_stage
        try:
            evidence_score = float(
                row.get("cycle_evidence_score")
                or legacy.get("evidence_score")
                or row.get("evidence_score")
                or 0.0
            )
        except (TypeError, ValueError):
            evidence_score = 0.0
        try:
            mainline_score = float(row.get("mainline_score_v6") or legacy.get("mainline_score_v6") or 0.0)
        except (TypeError, ValueError):
            mainline_score = 0.0
        score_weight_ratio = (
            0.0
            if "弱势" in stage or "退潮" in stage
            else max(evidence_score, mainline_score * 40.0)
        )
        phase = classify_shadow_phase(stage_source)
        theme_signals.append(
            {
                "rank": row.get("rank") or index,
                "theme_id": theme_id,
                "theme": theme_name,
                "stage": stage,
                "shadow_phase": phase["shadow_phase"],
                "instrument_preference": phase["instrument_preference"],
                "phase_reason": phase["phase_reason"],
                "cycle_stage": row.get("cycle_stage") or legacy.get("cycle_stage"),
                "cycle_stage_label": row.get("cycle_stage_label") or legacy.get("cycle_stage_label"),
                "lifecycle_state": row.get("lifecycle_state") or legacy.get("lifecycle_state"),
                "lifecycle_state_label": row.get("lifecycle_state_label")
                or legacy.get("lifecycle_state_label"),
                "cycle_market_score": row.get("cycle_market_score") or legacy.get("market_score"),
                "cycle_evidence_score": row.get("cycle_evidence_score") or legacy.get("evidence_score"),
                "mainline_score_v6": row.get("mainline_score_v6") or legacy.get("mainline_score_v6"),
                "evidence_score": evidence_score,
                "evidence_count": row.get("event_count_30d") or legacy.get("evidence_count"),
                "etf_score": legacy.get("etf_score") or row.get("etf_score"),
                "market_score": legacy.get("market_score") or row.get("market_score"),
                "top_indices": legacy.get("top_indices")
                or legacy.get("top_ths")
                or legacy.get("top_sw")
                or row.get("top_indices")
                or row.get("top_ths")
                or row.get("top_sw"),
                "top_etf": legacy.get("top_etf") or row.get("top_etf") or "",
                "score_weight_ratio": score_weight_ratio,
            }
        )

    return {
        "schema_version": "mainline_latest_for_shadow_account.v1",
        "mode": "simulation_input",
        "report_id": payload.get("report_id"),
        "basis_date": result.get("basis_date"),
        "generated_at": result.get("generated_at"),
        "constraints": {
            "read_only": True,
            "ratio_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "source": "theme.okbbc.com/api/latest",
        },
        "market_context": {
            "breadth": result.get("breadth"),
            "broad_indexes": result.get("broad_indexes") or [],
        },
        "theme_signals": theme_signals,
        "latest_result": result,
    }


def extract_market_basis(payload: dict[str, Any]) -> str | None:
    results = payload.get("results") or {}
    snapshot = results.get("market_snapshot") or {}
    score = results.get("market_score") or {}
    record = score.get("record") or {}
    return (
        snapshot.get("basis_trade_date")
        or (snapshot.get("payload") or {}).get("date")
        or record.get("basis_trade_date")
    )


def extract_optional_basis(source: str, payload: dict[str, Any]) -> str | None:
    if source == "etf":
        return etf_basis_date(payload)
    if source in {"stock", "leader"}:
        return stock_basis_date(payload)
    return payload.get("basis_date")


def make_snapshot(source: str, payload: dict[str, Any]) -> SourceSnapshot:
    if source == "theme":
        payload = normalize_theme_payload(payload)
    basis_date = payload.get("basis_date")
    if source == "market":
        basis_date = extract_market_basis(payload)
    elif source in {"etf", "stock", "leader"}:
        basis_date = extract_optional_basis(source, payload)
    return SourceSnapshot(
        source=source,
        fetched_at=now_iso(),
        basis_date=basis_date,
        schema_version=str(payload.get("schema_version", "")),
        content_hash=content_hash(payload),
        ok=True,
        payload=payload,
    )


def error_snapshot(source: str, error: Exception) -> SourceSnapshot:
    payload = {"error": type(error).__name__, "message": str(error)}
    return SourceSnapshot(
        source=source,
        fetched_at=now_iso(),
        basis_date=None,
        schema_version=None,
        content_hash=content_hash(payload),
        ok=False,
        payload=payload,
        error=f"{type(error).__name__}: {error}",
    )


def fetch_market_and_theme(
    market_url: str = MARKET_API_URL, theme_url: str = THEME_API_URL
) -> tuple[SourceSnapshot, SourceSnapshot]:
    try:
        market = make_snapshot("market", fetch_json(market_url))
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        market = error_snapshot("market", exc)

    try:
        theme = make_snapshot("theme", fetch_json(theme_url))
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        theme = error_snapshot("theme", exc)

    return market, theme


def fetch_optional_source(source: str, url: str) -> SourceSnapshot:
    try:
        return make_snapshot(source, fetch_json(url))
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        return error_snapshot(source, exc)


def fetch_research_sources(
    etf_url: str = ETF_API_URL,
    stock_url: str = STOCK_API_URL,
    leader_url: str = LEADER_API_URL,
) -> tuple[SourceSnapshot, SourceSnapshot, SourceSnapshot]:
    return (
        fetch_optional_source("etf", etf_url),
        fetch_optional_source("stock", stock_url),
        fetch_optional_source("leader", leader_url),
    )
