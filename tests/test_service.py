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


def test_allocations_for_run_restores_stock_instrument_type_from_gate_components(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "shadow.sqlite"
    _use_temp_db(monkeypatch, db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO shadow_runs
            (run_at, basis_date, market_basis_date, theme_report_id, market_regime,
             risk_budget_ratio, cash_ratio, previous_nav, nav, daily_return_ratio,
             reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-24T18:00:00+08:00",
                "2026-06-24",
                "2026-06-24",
                "theme_2026-06-24",
                "test",
                20.0,
                80.0,
                1.0,
                1.0,
                0.0,
                "test",
                "{}",
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO target_allocations
            (run_id, code, name, sleeve, theme, stage, target_weight_ratio,
             previous_weight_ratio, drift_ratio, price, pct_chg, evidence_score,
             pre_gate_weight_ratio, etf_gate_grade, etf_gate_score, etf_gate_pass,
             etf_execution_ratio, etf_gate_reasons_json,
             etf_gate_reject_reasons_json, etf_gate_data_gaps_json,
             etf_gate_components_json, source_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "688999.SH",
                "机器人龙头",
                "thematic",
                "机器人",
                "主线确认/资金收敛",
                6.0,
                0.0,
                6.0,
                30.0,
                2.0,
                88.0,
                6.0,
                "A",
                86.0,
                1,
                1.0,
                "[]",
                "[]",
                "[]",
                service.dumps({"instrument_gate": "stock_leader"}),
                "tushare.daily",
            ),
        )
        conn.commit()

        rows = service._allocations_for_run(conn, run_id)

    assert rows[0]["instrument_type"] == "stock"


def test_benchmark_curve_uses_saved_allocation_prices(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "shadow.sqlite"
    _use_temp_db(monkeypatch, db_path)
    monkeypatch.setattr(service, "fetch_tushare_close_series", lambda codes, dates: {})
    init_db(db_path)
    with connect(db_path) as conn:
        for index, basis_date in enumerate(("2026-06-24", "2026-06-25"), start=1):
            cursor = conn.execute(
                """
                INSERT INTO shadow_runs
                (run_at, basis_date, market_basis_date, theme_report_id, market_regime,
                 risk_budget_ratio, cash_ratio, previous_nav, nav, daily_return_ratio,
                 reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{basis_date}T18:00:00+08:00",
                    basis_date,
                    basis_date,
                    f"theme_{basis_date}",
                    "test",
                    10.0,
                    90.0,
                    1.0,
                    1.0 + index * 0.01,
                    0.01,
                    "test",
                    "{}",
                ),
            )
            run_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO nav_points
                (basis_date, nav, daily_return_ratio, risk_budget_ratio, cash_ratio, run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (basis_date, 1.0 + index * 0.01, 0.01, 10.0, 90.0, run_id, basis_date),
            )
            for code, name, price in (
                ("510300.SH", "华泰柏瑞沪深300ETF", 4.0 + index),
                ("510500.SH", "南方中证500ETF", 6.0 + index * 0.5),
            ):
                conn.execute(
                    """
                    INSERT INTO target_allocations
                    (run_id, code, name, sleeve, theme, stage, target_weight_ratio,
                     previous_weight_ratio, drift_ratio, price, pct_chg, evidence_score,
                     pre_gate_weight_ratio, etf_gate_grade, etf_gate_score, etf_gate_pass,
                     etf_execution_ratio, etf_gate_reasons_json,
                     etf_gate_reject_reasons_json, etf_gate_data_gaps_json,
                     etf_gate_components_json, source_note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        code,
                        name,
                        "core",
                        "核心",
                        "test",
                        5.0,
                        5.0,
                        0.0,
                        price,
                        0.0,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        "[]",
                        "[]",
                        "[]",
                        "{}",
                        "test.price",
                    ),
                )
        conn.commit()

        result = service.benchmark_curve(conn)

    hs300 = next(row for row in result if row["code"] == "510300.SH")
    zz500 = next(row for row in result if row["code"] == "510500.SH")
    assert hs300["points"][0]["normalized"] == 1.0
    assert hs300["points"][1]["close"] == 6.0
    assert hs300["points"][1]["normalized"] == 1.2
    assert zz500["points"][1]["normalized"] == 7.0 / 6.5
