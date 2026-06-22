from __future__ import annotations

from typing import Any

from .allocator import (
    SYNTHETIC_INSTRUMENTS,
    allocation_candidate_codes,
    allocation_plan,
    allocation_weight_map,
    legacy_core_price_point_from_etfs,
    market_record,
    sleeve_summary,
)
from .db import connect, dumps, init_db, loads, row_to_dict, rows_to_dicts
from .pricing import PricePoint, fetch_tushare_prices, merge_price_maps, theme_price_fallback
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


def _run_payloads_from_snapshots(
    conn: Any,
    market_snapshot: SourceSnapshot,
    theme_snapshot: SourceSnapshot,
    *,
    allow_stale: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    if allow_stale:
        market_payload = market_snapshot.payload if market_snapshot.ok else _latest_ok_payload(conn, "market")
        theme_payload = theme_snapshot.payload if theme_snapshot.ok else _latest_ok_payload(conn, "theme")
        if not market_payload or not theme_payload:
            raise RuntimeError("上游接口不可用，且本地没有可回退的历史快照")
    else:
        errors = []
        if not market_snapshot.ok:
            errors.append(f"market={market_snapshot.error or 'fetch_failed'}")
        if not theme_snapshot.ok:
            errors.append(f"theme={theme_snapshot.error or 'fetch_failed'}")
        if errors:
            raise RuntimeError("上游接口不可用，已停止生成正式影子仓位: " + "; ".join(errors))
        market_payload = market_snapshot.payload
        theme_payload = theme_snapshot.payload

    market_basis_date = extract_market_basis(market_payload)
    theme_basis_date = theme_payload.get("basis_date")
    if not market_basis_date or not theme_basis_date:
        raise RuntimeError(
            "上游结果缺少基准日，已停止生成正式影子仓位: "
            f"market_basis_date={market_basis_date or 'missing'}, "
            f"theme_basis_date={theme_basis_date or 'missing'}"
        )
    if market_basis_date != theme_basis_date:
        raise RuntimeError(
            "上游基准日不一致，已停止生成正式影子仓位: "
            f"market_basis_date={market_basis_date}, theme_basis_date={theme_basis_date}"
        )
    return market_payload, theme_payload, theme_basis_date, market_basis_date


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
        SELECT code, name, sleeve, theme, stage, target_weight_ratio, previous_weight_ratio,
               drift_ratio, price, pct_chg, evidence_score, pre_gate_weight_ratio,
               etf_gate_grade, etf_gate_score, etf_gate_pass, etf_execution_ratio,
               etf_gate_reasons_json, etf_gate_reject_reasons_json,
               etf_gate_data_gaps_json, etf_gate_components_json, source_note
        FROM target_allocations
        WHERE run_id = ?
        ORDER BY
            CASE sleeve
                WHEN 'core' THEN 0
                WHEN 'mainline' THEN 1
                WHEN 'thematic' THEN 2
                WHEN 'defensive' THEN 3
                ELSE 9
            END,
            target_weight_ratio DESC,
            code ASC
        """,
        (run_id,),
    ).fetchall()
    result = rows_to_dicts(rows)
    for row in result:
        synthetic = SYNTHETIC_INSTRUMENTS.get(row.get("code"))
        row["display_code"] = synthetic["display_code"] if synthetic else row.get("code")
        row["instrument_type"] = synthetic["instrument_type"] if synthetic else "etf"
        row["is_synthetic"] = bool(synthetic)
        row["etf_gate_pass"] = (
            bool(row["etf_gate_pass"]) if row.get("etf_gate_pass") is not None else None
        )
        row["etf_gate_reasons"] = loads(row.pop("etf_gate_reasons_json", None)) or []
        row["etf_gate_reject_reasons"] = (
            loads(row.pop("etf_gate_reject_reasons_json", None)) or []
        )
        row["etf_gate_data_gaps"] = loads(row.pop("etf_gate_data_gaps_json", None)) or []
        row["etf_gate_components"] = loads(row.pop("etf_gate_components_json", None)) or {}
    return result


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


def run_daily_rebalance(reason: str = "manual", *, allow_stale: bool = False) -> dict[str, Any]:
    init_db()
    market_snapshot, theme_snapshot = fetch_market_and_theme()

    with connect() as conn:
        _store_source_snapshot(conn, market_snapshot)
        _store_source_snapshot(conn, theme_snapshot)
        conn.commit()

        market_payload, theme_payload, basis_date, market_basis_date = _run_payloads_from_snapshots(
            conn,
            market_snapshot,
            theme_snapshot,
            allow_stale=allow_stale,
        )

        previous_run = _latest_run_before(conn, basis_date)
        previous_allocations = _allocations_for_run(
            conn, int(previous_run["id"]) if previous_run else None
        )

        codes = sorted(
            {
                *allocation_candidate_codes(market_payload, theme_payload),
                *(row["code"] for row in previous_allocations if row.get("code")),
            }
        )
        fallback_prices = theme_price_fallback(theme_payload)
        tushare_prices = fetch_tushare_prices(codes, basis_date) if codes else {}
        price_map = merge_price_maps(tushare_prices, fallback_prices)
        if "CORE.ASHARE" in codes:
            legacy_core_point = legacy_core_price_point_from_etfs(price_map)
            if legacy_core_point and legacy_core_point.pct_chg is not None:
                price_map["CORE.ASHARE"] = legacy_core_point

        plan = allocation_plan(market_payload, theme_payload, price_map)
        risk_budget = float(plan["risk_budget_ratio"])
        targets = plan["targets"]
        for row in targets:
            if row["code"] not in price_map and row.get("pct_chg") is not None:
                price_map[row["code"]] = PricePoint(
                    code=row["code"],
                    close=row.get("price"),
                    pct_chg=row.get("pct_chg"),
                    source=row.get("source_note") or "allocation.synthetic",
                )
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
        summary = sleeve_summary(targets)
        cash_ratio = summary["defensive"]
        record = market_record(market_payload)

        payload = {
            "market_fetch_ok": market_snapshot.ok,
            "theme_fetch_ok": theme_snapshot.ok,
            "market_fetch_error": market_snapshot.error,
            "theme_fetch_error": theme_snapshot.error,
            "market_score": record.get("market_position_score"),
            "equity_position_range": record.get("equity_position_range"),
            "sleeve_summary": summary,
            "market_risk_budget_ratio": plan["market_risk_budget_ratio"],
            "sleeve_targets_before_gate": plan["sleeve_targets_before_gate"],
            "executed_active_weight_ratio": risk_budget,
            "decision_trace": {
                "market_risk_budget_ratio": plan["market_risk_budget_ratio"],
                "sleeve_targets_before_gate": plan["sleeve_targets_before_gate"],
                "etf_gate_summary": plan["etf_gate_summary"],
                "etf_gate": plan["etf_gate"],
            },
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
                    row["code"],
                    row["name"],
                    row.get("sleeve") or "mainline",
                    row["theme"],
                    row["stage"],
                    float(row.get("target_weight_ratio") or 0.0),
                    float(row.get("previous_weight_ratio") or 0.0),
                    float(row.get("drift_ratio") or 0.0),
                    row.get("price"),
                    row.get("pct_chg"),
                    row.get("evidence_score"),
                    row.get("pre_gate_weight_ratio"),
                    row.get("etf_gate_grade"),
                    row.get("etf_gate_score"),
                    (
                        1
                        if row.get("etf_gate_pass") is True
                        else 0
                        if row.get("etf_gate_pass") is False
                        else None
                    ),
                    row.get("etf_execution_ratio"),
                    dumps(row.get("etf_gate_reasons") or []),
                    dumps(row.get("etf_gate_reject_reasons") or []),
                    dumps(row.get("etf_gate_data_gaps") or []),
                    dumps(row.get("etf_gate_components") or {}),
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
            "run_payload": loads(run["payload_json"]) if run else None,
            "allocations": _allocations_for_run(conn, run_id),
            "sleeve_summary": summary,
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


def _latest_runs_by_basis(conn: Any, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*
        FROM shadow_runs r
        JOIN (
            SELECT basis_date, MAX(id) AS max_id
            FROM shadow_runs
            GROUP BY basis_date
        ) latest ON latest.max_id = r.id
        ORDER BY r.basis_date DESC, r.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_to_dicts(rows)


def _change_action(previous_weight: float, target_weight: float) -> str:
    threshold = 0.005
    drift = target_weight - previous_weight
    if abs(drift) < threshold:
        return "hold"
    if previous_weight < threshold and target_weight >= threshold:
        return "new"
    if target_weight < threshold and previous_weight >= threshold:
        return "exit"
    if drift > 0:
        return "increase"
    return "decrease"


def _change_label(action: str) -> str:
    return {
        "new": "调入",
        "increase": "增加",
        "decrease": "降低",
        "exit": "调出",
        "hold": "不变",
    }.get(action, action)


def _rebalance_changes(
    current_allocations: list[dict[str, Any]],
    previous_allocations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_by_code = {row["code"]: row for row in current_allocations}
    previous_by_code = {row["code"]: row for row in previous_allocations}
    changes: list[dict[str, Any]] = []
    for code in sorted(set(current_by_code) | set(previous_by_code)):
        current = current_by_code.get(code)
        previous = previous_by_code.get(code)
        target_weight = float((current or {}).get("target_weight_ratio") or 0.0)
        previous_weight = float((previous or {}).get("target_weight_ratio") or 0.0)
        action = _change_action(previous_weight, target_weight)
        if action == "hold":
            continue
        source = current or previous or {}
        changes.append(
            {
                "code": code,
                "display_code": source.get("display_code") or code,
                "instrument_type": source.get("instrument_type") or "etf",
                "is_synthetic": bool(source.get("is_synthetic")),
                "name": source.get("name") or code,
                "sleeve": source.get("sleeve"),
                "theme": source.get("theme") or "",
                "stage": source.get("stage") or "",
                "action": action,
                "action_label": _change_label(action),
                "previous_weight_ratio": previous_weight,
                "target_weight_ratio": target_weight,
                "drift_ratio": target_weight - previous_weight,
                "etf_gate_grade": (current or {}).get("etf_gate_grade"),
                "etf_gate_score": (current or {}).get("etf_gate_score"),
                "etf_execution_ratio": (current or {}).get("etf_execution_ratio"),
            }
        )

    action_order = {"new": 0, "increase": 1, "decrease": 2, "exit": 3}
    return sorted(
        changes,
        key=lambda row: (
            action_order.get(str(row.get("action")), 9),
            -abs(float(row.get("drift_ratio") or 0.0)),
            str(row.get("code") or ""),
        ),
    )


def rebalance_history(conn: Any | None = None, limit: int = 8) -> list[dict[str, Any]]:
    should_close = conn is None
    conn = conn or connect()
    try:
        runs = _latest_runs_by_basis(conn, max(2, limit + 1))
        history: list[dict[str, Any]] = []
        for index, run in enumerate(runs[:limit]):
            previous_run = runs[index + 1] if index + 1 < len(runs) else None
            current_allocations = _allocations_for_run(conn, int(run["id"]))
            previous_allocations = (
                _allocations_for_run(conn, int(previous_run["id"])) if previous_run else []
            )
            changes = _rebalance_changes(current_allocations, previous_allocations)
            if not changes and previous_run:
                continue
            previous_active = (
                float(previous_run.get("risk_budget_ratio") or 0.0) if previous_run else 0.0
            )
            previous_cash = float(previous_run.get("cash_ratio") or 0.0) if previous_run else 0.0
            active = float(run.get("risk_budget_ratio") or 0.0)
            cash = float(run.get("cash_ratio") or 0.0)
            history.append(
                {
                    "run_id": run.get("id"),
                    "run_at": run.get("run_at"),
                    "basis_date": run.get("basis_date"),
                    "reason": run.get("reason"),
                    "previous_run_id": previous_run.get("id") if previous_run else None,
                    "previous_basis_date": previous_run.get("basis_date") if previous_run else None,
                    "active_weight_ratio": active,
                    "previous_active_weight_ratio": previous_active if previous_run else None,
                    "active_drift_ratio": active - previous_active if previous_run else None,
                    "cash_ratio": cash,
                    "previous_cash_ratio": previous_cash if previous_run else None,
                    "cash_drift_ratio": cash - previous_cash if previous_run else None,
                    "nav": run.get("nav"),
                    "daily_return_ratio": run.get("daily_return_ratio"),
                    "change_count": len(changes),
                    "total_abs_drift_ratio": sum(
                        abs(float(row.get("drift_ratio") or 0.0)) for row in changes
                    ),
                    "changes": changes,
                }
            )
        return history
    finally:
        if should_close:
            conn.close()


def latest_state() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        run = latest_run(conn)
        run_payload = loads(run["payload_json"]) if run else None
        decision_trace = (run_payload or {}).get("decision_trace") or {}
        allocations = _allocations_for_run(conn, int(run["id"]) if run else None)
        return {
            "run": run,
            "run_payload": run_payload,
            "allocations": allocations,
            "sleeve_summary": sleeve_summary(allocations),
            "etf_gate_summary": decision_trace.get("etf_gate_summary") or {},
            "etf_gate": decision_trace.get("etf_gate") or [],
            "nav_curve": nav_curve(conn),
            "rebalance_history": rebalance_history(conn),
            "source_status": source_status(conn),
        }


def build_index_payload(state: dict[str, Any]) -> dict[str, Any]:
    run = state.get("run") or {}
    run_payload = state.get("run_payload") or {}
    decision_trace = run_payload.get("decision_trace") or {}
    etf_gate_summary = (
        state.get("etf_gate_summary") or decision_trace.get("etf_gate_summary") or {}
    )
    etf_gate = state.get("etf_gate") or decision_trace.get("etf_gate") or []
    sleeve_weights = state.get("sleeve_summary") or {}
    metrics = {
        "nav": run.get("nav"),
        "active_weight_ratio": run.get("risk_budget_ratio"),
        "defensive_weight_ratio": run.get("cash_ratio"),
        "basis_date": run.get("basis_date"),
    }
    return {
        "page": {
            "title": "MyInvestShadow",
            "subtitle": "基于市场结果与主线结果的影子账户",
        },
        "run": {
            "id": run.get("id"),
            "run_at": run.get("run_at"),
            "basis_date": run.get("basis_date"),
            "market_basis_date": run.get("market_basis_date"),
            "theme_report_id": run.get("theme_report_id"),
            "market_regime": run.get("market_regime"),
            "risk_budget_ratio": run.get("risk_budget_ratio"),
            "cash_ratio": run.get("cash_ratio"),
            "nav": run.get("nav"),
            "daily_return_ratio": run.get("daily_return_ratio"),
            "reason": run.get("reason"),
        },
        "metrics": metrics,
        "sleeves": [
            {"key": "core", "label": "核心仓位", "weight_ratio": sleeve_weights.get("core", 0.0)},
            {
                "key": "mainline",
                "label": "主线仓位",
                "weight_ratio": sleeve_weights.get("mainline", 0.0),
            },
            {
                "key": "thematic",
                "label": "主题仓位",
                "weight_ratio": sleeve_weights.get("thematic", 0.0),
            },
            {
                "key": "defensive",
                "label": "防御仓位",
                "weight_ratio": sleeve_weights.get("defensive", 0.0),
            },
        ],
        "sleeve_summary": sleeve_weights,
        "etf_gate_summary": etf_gate_summary,
        "etf_gate": etf_gate,
        "nav_curve": state.get("nav_curve") or [],
        "allocations": state.get("allocations") or [],
        "rebalance_history": state.get("rebalance_history") or [],
        "source_status": state.get("source_status") or [],
        "links": {
            "full_state": "/api/latest",
            "refresh": "/api/run/daily",
            "nav": "/api/nav",
            "allocations": "/api/allocations/latest",
            "rebalance_history": "/api/rebalance-history",
        },
    }


def index_state() -> dict[str, Any]:
    return build_index_payload(latest_state())


def ensure_seed_data() -> None:
    init_db()
    with connect() as conn:
        run = latest_run(conn)
    if run:
        return
    run_daily_rebalance(reason="startup_seed")
