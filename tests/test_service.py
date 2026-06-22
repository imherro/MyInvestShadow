from __future__ import annotations

import pytest

from shadow_app import service
from shadow_app.db import connect, init_db
from shadow_app.upstream import SourceSnapshot


def _market_payload(basis_date: str) -> dict:
    return {
        "schema_version": "test.market.v1",
        "results": {
            "market_snapshot": {"basis_trade_date": basis_date},
            "market_score": {
                "record": {
                    "basis_trade_date": basis_date,
                    "equity_position_range": "35%-45%",
                    "market_position_score": 46.98,
                }
            },
        },
    }


def _theme_payload(basis_date: str) -> dict:
    return {
        "schema_version": "test.theme.v1",
        "basis_date": basis_date,
        "report_id": f"theme_{basis_date}",
        "theme_signals": [],
    }


def _snapshot(
    source: str,
    *,
    ok: bool,
    basis_date: str | None,
    payload: dict,
    error: str | None = None,
) -> SourceSnapshot:
    return SourceSnapshot(
        source=source,
        fetched_at="2026-06-22T18:00:00+08:00",
        basis_date=basis_date,
        schema_version=str(payload.get("schema_version", "")),
        content_hash=f"{source}-{basis_date}-{ok}",
        ok=ok,
        payload=payload,
        error=error,
    )


def _use_temp_db(monkeypatch: pytest.MonkeyPatch, db_path) -> None:
    monkeypatch.setattr(service, "init_db", lambda: init_db(db_path))
    monkeypatch.setattr(service, "connect", lambda: connect(db_path))


def test_run_daily_rebalance_rejects_failed_fresh_snapshot_without_fallback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "shadow.sqlite"
    _use_temp_db(monkeypatch, db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        service._store_source_snapshot(
            conn,
            _snapshot(
                "market",
                ok=True,
                basis_date="2026-06-21",
                payload=_market_payload("2026-06-21"),
            ),
        )
        service._store_source_snapshot(
            conn,
            _snapshot(
                "theme",
                ok=True,
                basis_date="2026-06-21",
                payload=_theme_payload("2026-06-21"),
            ),
        )
        conn.commit()

    market_failure = _snapshot(
        "market",
        ok=False,
        basis_date=None,
        payload={"error": "HTTPError"},
        error="HTTPError: 502 Bad Gateway",
    )
    theme_ok = _snapshot(
        "theme",
        ok=True,
        basis_date="2026-06-22",
        payload=_theme_payload("2026-06-22"),
    )
    monkeypatch.setattr(service, "fetch_market_and_theme", lambda: (market_failure, theme_ok))

    with pytest.raises(RuntimeError, match="上游接口不可用"):
        service.run_daily_rebalance(reason="test")

    with connect(db_path) as conn:
        snapshot_count = conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0]
        run_count = conn.execute("SELECT COUNT(*) FROM shadow_runs").fetchone()[0]

    assert snapshot_count == 4
    assert run_count == 0


def test_run_daily_rebalance_rejects_mismatched_basis_dates(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "shadow.sqlite"
    _use_temp_db(monkeypatch, db_path)
    market_ok = _snapshot(
        "market",
        ok=True,
        basis_date="2026-06-21",
        payload=_market_payload("2026-06-21"),
    )
    theme_ok = _snapshot(
        "theme",
        ok=True,
        basis_date="2026-06-22",
        payload=_theme_payload("2026-06-22"),
    )
    monkeypatch.setattr(service, "fetch_market_and_theme", lambda: (market_ok, theme_ok))

    with pytest.raises(RuntimeError, match="上游基准日不一致"):
        service.run_daily_rebalance(reason="test")

    with connect(db_path) as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM shadow_runs").fetchone()[0]

    assert run_count == 0
