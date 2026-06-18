from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .allocator import (
    allocation_weight_map,
    compare_actual_to_target,
    market_record,
    target_allocations,
)
from .db import connect, dumps, init_db, loads, row_to_dict, rows_to_dicts
from .pricing import fetch_tushare_prices, merge_price_maps, theme_price_fallback
from .upstream import SourceSnapshot, extract_market_basis, fetch_market_and_theme, now_iso


def _store_source_snapshot(conn: Any, snapshot: SourceSnapshot) -> None:
    conn.execute(
        """
        INSERT INTO source_snapshots
        (source, fetched_at, basis_date, schema_version, content_hash, ok, error, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.source,
            snapshot.fetched_at,
            snapshot.basis_date,
            snapshot.schema_version,
            snapshot.content_hash,
            1 if snapshot.ok else 0,
            snapshot.error,
            dumps(snapshot.payload),
        ),
    )


def _latest_ok_payload(conn: Any, source: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT payload_json
        FROM source_snapshots
        WHERE source = ? AND ok = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (source,),
    ).fetchone()
    if not row:
        return None
    return loads(row["payload_json"])


def _latest_run_before(conn: Any, basis_date: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM shadow_runs
        WHERE basis_date < ?
        ORDER BY basis_date DESC, id DESC
        LIMIT 1
        """,
        (basis_date,),
    ).fetchone()
    return row_to_dict(row)


def _latest_nav_before(conn: Any, basis_date: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM nav_points
        WHERE basis_date < ?
        ORDER BY basis_date DESC
        LIMIT 1
        """,
        (basis_date,),
    ).fetchone()
    return row_to_dict(row)


def _allocations_for_run(conn: Any, run_id: int | None) -> list[dict[str, Any]]:
    if not run_id:
        return []
    rows = conn.execute(
        """
        SELECT code, name, theme, stage, target_weight_ratio, previous_weight_ratio,
               drift_ratio, price, pct_chg, evidence_score, source_note
        FROM target_allocations
        WHERE run_id = ?
        ORDER BY target_weight_ratio DESC, code ASC
        """,
        (run_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def _latest_target_rows(conn: Any) -> list[dict[str, Any]]:
    run = latest_run(conn)
    if not run:
        return []
    return _allocations_for_run(conn, int(run["id"]))


def latest_run(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM shadow_runs ORDER BY basis_date DESC, id DESC LIMIT 1"
    ).fetchone()
    return row_to_dict(row)


def _daily_return_from_previous_allocations(
    previous_allocations: list[dict[str, Any]], price_map: dict[str, Any]
) -> float:
    total = 0.0
    for row in previous_allocations:
        code = row["code"]
        weight_ratio = float(row.get("target_weight_ratio") or 0.0)
        point = price_map.get(code)
        if not point or point.pct_chg is None:
            continue
        total += (weight_ratio / 100.0) * (float(point.pct_chg) / 100.0)
    return total


def run_daily_rebalance(reason: str = "manual") -> dict[str, Any]:
    init_db()
    market_snapshot, theme_snapshot = fetch_market_and_theme()

    with connect() as conn:
        _store_source_snapshot(conn, market_snapshot)
        _store_source_snapshot(conn, theme_snapshot)

        market_payload = market_snapshot.payload if market_snapshot.ok else _latest_ok_payload(conn, "market")
        theme_payload = theme_snapshot.payload if theme_snapshot.ok else _latest_ok_payload(conn, "theme")
        if not market_payload or not theme_payload:
            raise RuntimeError("上游接口不可用，且本地没有可回退的历史快照")

        basis_date = theme_payload.get("basis_date") or extract_market_basis(market_payload)
        if not basis_date:
            basis_date = date.today().isoformat()
        market_basis_date = extract_market_basis(market_payload)

        preliminary_budget, preliminary_targets = target_allocations(
            market_payload, theme_payload, {}
        )
        previous_run = _latest_run_before(conn, basis_date)
        previous_allocations = _allocations_for_run(
            conn, int(previous_run["id"]) if previous_run else None
        )

        codes = sorted(
            {
                row["code"]
                for row in [*preliminary_targets, *previous_allocations]
                if row.get("code")
            }
        )
        fallback_prices = theme_price_fallback(theme_payload)
        tushare_prices = fetch_tushare_prices(codes, basis_date) if codes else {}
        price_map = merge_price_maps(tushare_prices, fallback_prices)

        risk_budget, targets = target_allocations(market_payload, theme_payload, price_map)
        previous_weights = allocation_weight_map(previous_allocations)
        for row in targets:
            previous_weight = previous_weights.get(row["code"], 0.0)
            row["previous_weight_ratio"] = previous_weight
            row["drift_ratio"] = float(row["target_weight_ratio"]) - previous_weight

        latest_nav = _latest_nav_before(conn, basis_date)
        previous_nav = float(latest_nav["nav"]) if latest_nav else 1.0
        daily_return = (
            _daily_return_from_previous_allocations(previous_allocations, price_map)
            if previous_allocations
            else 0.0
        )
        nav = previous_nav * (1.0 + daily_return)
        target_sum = sum(float(row.get("target_weight_ratio") or 0.0) for row in targets)
        cash_ratio = max(0.0, 100.0 - target_sum)
        record = market_record(market_payload)

        payload = {
            "market_fetch_ok": market_snapshot.ok,
            "theme_fetch_ok": theme_snapshot.ok,
            "market_fetch_error": market_snapshot.error,
            "theme_fetch_error": theme_snapshot.error,
            "market_score": record.get("market_position_score"),
            "equity_position_range": record.get("equity_position_range"),
            "theme_report_id": theme_payload.get("report_id"),
            "basis_date": basis_date,
            "price_sources": {
                code: {
                    "source": point.source,
                    "has_close": point.close is not None,
                    "has_pct_chg": point.pct_chg is not None,
                    "error": point.error,
                }
                for code, point in price_map.items()
            },
        }

        cursor = conn.execute(
            """
            INSERT INTO shadow_runs
            (run_at, basis_date, market_basis_date, theme_report_id, market_regime,
             risk_budget_ratio, cash_ratio, previous_nav, nav, daily_return_ratio,
             reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                basis_date,
                market_basis_date,
                theme_payload.get("report_id"),
                record.get("market_regime"),
                risk_budget,
                cash_ratio,
                previous_nav,
                nav,
                daily_return,
                reason,
                dumps(payload),
            ),
        )
        run_id = int(cursor.lastrowid)
        for row in targets:
            conn.execute(
                """
                INSERT INTO target_allocations
                (run_id, code, name, theme, stage, target_weight_ratio,
                 previous_weight_ratio, drift_ratio, price, pct_chg, evidence_score, source_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row["code"],
                    row["name"],
                    row["theme"],
                    row["stage"],
                    float(row.get("target_weight_ratio") or 0.0),
                    float(row.get("previous_weight_ratio") or 0.0),
                    float(row.get("drift_ratio") or 0.0),
                    row.get("price"),
                    row.get("pct_chg"),
                    row.get("evidence_score"),
                    row.get("source_note") or "",
                ),
            )

        conn.execute(
            """
            INSERT INTO nav_points
            (basis_date, nav, daily_return_ratio, risk_budget_ratio, cash_ratio, run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(basis_date) DO UPDATE SET
                nav = excluded.nav,
                daily_return_ratio = excluded.daily_return_ratio,
                risk_budget_ratio = excluded.risk_budget_ratio,
                cash_ratio = excluded.cash_ratio,
                run_id = excluded.run_id,
                created_at = excluded.created_at
            """,
            (basis_date, nav, daily_return, risk_budget, cash_ratio, run_id, now_iso()),
        )

        conn.commit()
        run = latest_run(conn) or {}
        return {
            "run": run,
            "allocations": _allocations_for_run(conn, run_id),
            "nav_curve": nav_curve(conn),
            "source_status": source_status(conn),
        }


def nav_curve(conn: Any | None = None) -> list[dict[str, Any]]:
    should_close = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            """
            SELECT basis_date, nav, daily_return_ratio, risk_budget_ratio, cash_ratio
            FROM nav_points
            ORDER BY basis_date ASC
            """
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        if should_close:
            conn.close()


def source_status(conn: Any | None = None) -> list[dict[str, Any]]:
    should_close = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            """
            SELECT s1.source, s1.fetched_at, s1.basis_date, s1.schema_version,
                   s1.content_hash, s1.ok, s1.error
            FROM source_snapshots s1
            JOIN (
                SELECT source, MAX(id) AS max_id
                FROM source_snapshots
                GROUP BY source
            ) latest ON latest.max_id = s1.id
            ORDER BY s1.source
            """
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        if should_close:
            conn.close()


def latest_state() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        run = latest_run(conn)
        allocations = _allocations_for_run(conn, int(run["id"]) if run else None)
        actual = latest_actual(conn)
        comparison = (
            compare_actual_to_target(actual.get("holdings", []), allocations)
            if actual
            else []
        )
        return {
            "run": run,
            "run_payload": loads(run["payload_json"]) if run else None,
            "allocations": allocations,
            "nav_curve": nav_curve(conn),
            "source_status": source_status(conn),
            "actual_holdings": actual,
            "comparison": comparison,
        }


def save_actual_holdings(
    holdings: list[dict[str, Any]], as_of_date: str | None = None, source: str = "manual"
) -> dict[str, Any]:
    init_db()
    as_of_date = as_of_date or date.today().isoformat()
    normalized = []
    for row in holdings:
        code = str(row.get("code") or "").strip().upper()
        if not code:
            continue
        normalized.append(
            {
                "code": code,
                "name": str(row.get("name") or code).strip(),
                "theme": str(row.get("theme") or "").strip(),
                "weight_ratio": float(row.get("weight_ratio") or 0.0),
            }
        )
    payload = {"as_of_date": as_of_date, "source": source, "holdings": normalized}
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO actual_holdings (as_of_date, source, created_at, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (as_of_date, source, now_iso(), dumps(payload)),
        )
        targets = _latest_target_rows(conn)
        comparison = compare_actual_to_target(normalized, targets)
        conn.commit()
    return {"actual_holdings": payload, "comparison": comparison}


def latest_actual(conn: Any | None = None) -> dict[str, Any] | None:
    should_close = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            """
            SELECT payload_json
            FROM actual_holdings
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return loads(row["payload_json"]) if row else None
    finally:
        if should_close:
            conn.close()


def ensure_seed_data() -> None:
    init_db()
    with connect() as conn:
        run = latest_run(conn)
    if run:
        return
    run_daily_rebalance(reason="startup_seed")
