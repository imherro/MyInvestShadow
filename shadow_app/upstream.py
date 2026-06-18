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
