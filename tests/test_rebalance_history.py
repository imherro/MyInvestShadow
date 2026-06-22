from __future__ import annotations

from shadow_app.db import connect, dumps, init_db
from shadow_app.service import rebalance_history


def _insert_run(conn, *, basis_date: str, risk: float, cash: float) -> int:
    cursor = conn.execute(
        """
        INSERT INTO shadow_runs
        (run_at, basis_date, market_basis_date, theme_report_id, market_regime,
         risk_budget_ratio, cash_ratio, previous_nav, nav, daily_return_ratio,
         reason, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{basis_date}T21:10:00+08:00",
            basis_date,
            basis_date,
            "test_report",
            "test",
            risk,
            cash,
            1.0,
            1.0,
            0.0,
            "test",
            dumps({}),
        ),
    )
    return int(cursor.lastrowid)


def _insert_allocation(
    conn,
    *,
    run_id: int,
    code: str,
    name: str,
    sleeve: str,
    weight: float,
) -> None:
    conn.execute(
        """
        INSERT INTO target_allocations
        (run_id, code, name, sleeve, theme, stage, target_weight_ratio,
         previous_weight_ratio, drift_ratio, price, pct_chg, evidence_score, source_note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            code,
            name,
            sleeve,
            "test",
            "test",
            weight,
            0.0,
            0.0,
            None,
            None,
            None,
            "test",
        ),
    )


def test_rebalance_history_includes_exited_positions(tmp_path) -> None:
    db_path = tmp_path / "shadow.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        old_run = _insert_run(conn, basis_date="2026-06-17", risk=30.0, cash=70.0)
        _insert_allocation(
            conn,
            run_id=old_run,
            code="CORE.ASHARE",
            name="A股核心宽基组合",
            sleeve="core",
            weight=18.0,
        )
        _insert_allocation(
            conn,
            run_id=old_run,
            code="159516.SZ",
            name="半导体ETF",
            sleeve="mainline",
            weight=6.5,
        )
        _insert_allocation(
            conn,
            run_id=old_run,
            code="DEFENSIVE.CASH",
            name="防御现金仓",
            sleeve="defensive",
            weight=75.5,
        )

        new_run = _insert_run(conn, basis_date="2026-06-18", risk=25.0, cash=75.0)
        _insert_allocation(
            conn,
            run_id=new_run,
            code="CORE.ASHARE",
            name="A股核心宽基组合",
            sleeve="core",
            weight=19.5,
        )
        _insert_allocation(
            conn,
            run_id=new_run,
            code="588200.SH",
            name="芯片ETF",
            sleeve="mainline",
            weight=5.5,
        )
        _insert_allocation(
            conn,
            run_id=new_run,
            code="DEFENSIVE.CASH",
            name="防御现金仓",
            sleeve="defensive",
            weight=75.0,
        )
        conn.commit()

        history = rebalance_history(conn)

    assert history[0]["basis_date"] == "2026-06-18"
    assert history[0]["previous_basis_date"] == "2026-06-17"
    changes = {row["code"]: row for row in history[0]["changes"]}
    assert changes["159516.SZ"]["action"] == "exit"
    assert changes["159516.SZ"]["target_weight_ratio"] == 0.0
    assert changes["588200.SH"]["action"] == "new"
    assert changes["CORE.ASHARE"]["action"] == "increase"
