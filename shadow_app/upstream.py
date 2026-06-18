from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .config import HTTP_HEADERS, MARKET_API_URL, THEME_API_URL
from .db import dumps


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
    ranking = result.get("theme_ranking") or []
    theme_signals: list[dict[str, Any]] = []
    for index, row in enumerate(ranking, start=1):
        stage = str(row.get("stage") or "")
        try:
            evidence_score = float(row.get("evidence_score") or 0.0)
        except (TypeError, ValueError):
            evidence_score = 0.0
        score_weight_ratio = 0.0 if "弱势" in stage or "退潮" in stage else evidence_score
        theme_signals.append(
            {
                "rank": row.get("rank") or index,
                "theme": row.get("theme") or "",
                "stage": stage,
                "evidence_score": evidence_score,
                "evidence_count": row.get("evidence_count"),
                "etf_score": row.get("etf_score"),
                "top_indices": row.get("top_indices") or row.get("top_ths") or row.get("top_sw"),
                "top_etf": row.get("top_etf") or "",
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


def make_snapshot(source: str, payload: dict[str, Any]) -> SourceSnapshot:
    if source == "theme":
        payload = normalize_theme_payload(payload)
    basis_date = payload.get("basis_date")
    if source == "market":
        basis_date = extract_market_basis(payload)
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
