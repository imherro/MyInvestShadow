from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS source_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    basis_date TEXT,
    schema_version TEXT,
    content_hash TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    basis_date TEXT NOT NULL,
    market_basis_date TEXT,
    theme_report_id TEXT,
    market_regime TEXT,
    risk_budget_ratio REAL NOT NULL,
    cash_ratio REAL NOT NULL,
    previous_nav REAL NOT NULL,
    nav REAL NOT NULL,
    daily_return_ratio REAL NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES shadow_runs(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sleeve TEXT NOT NULL DEFAULT 'mainline',
    theme TEXT NOT NULL,
    stage TEXT NOT NULL,
    target_weight_ratio REAL NOT NULL,
    previous_weight_ratio REAL NOT NULL,
    drift_ratio REAL NOT NULL,
    price REAL,
    pct_chg REAL,
    evidence_score REAL,
    pre_gate_weight_ratio REAL,
    etf_gate_grade TEXT,
    etf_gate_score REAL,
    etf_gate_pass INTEGER,
    etf_execution_ratio REAL,
    etf_gate_reasons_json TEXT,
    etf_gate_reject_reasons_json TEXT,
    etf_gate_data_gaps_json TEXT,
    etf_gate_components_json TEXT,
    source_note TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nav_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    basis_date TEXT NOT NULL UNIQUE,
    nav REAL NOT NULL,
    daily_return_ratio REAL NOT NULL,
    risk_budget_ratio REAL NOT NULL,
    cash_ratio REAL NOT NULL,
    run_id INTEGER NOT NULL REFERENCES shadow_runs(id),
    created_at TEXT NOT NULL
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("DROP TABLE IF EXISTS actual_holdings")
        _ensure_column(
            conn,
            "target_allocations",
            "sleeve",
            "sleeve TEXT NOT NULL DEFAULT 'mainline'",
        )
        _ensure_column(conn, "target_allocations", "pre_gate_weight_ratio", "pre_gate_weight_ratio REAL")
        _ensure_column(conn, "target_allocations", "etf_gate_grade", "etf_gate_grade TEXT")
        _ensure_column(conn, "target_allocations", "etf_gate_score", "etf_gate_score REAL")
        _ensure_column(conn, "target_allocations", "etf_gate_pass", "etf_gate_pass INTEGER")
        _ensure_column(conn, "target_allocations", "etf_execution_ratio", "etf_execution_ratio REAL")
        _ensure_column(conn, "target_allocations", "etf_gate_reasons_json", "etf_gate_reasons_json TEXT")
        _ensure_column(
            conn,
            "target_allocations",
            "etf_gate_reject_reasons_json",
            "etf_gate_reject_reasons_json TEXT",
        )
        _ensure_column(conn, "target_allocations", "etf_gate_data_gaps_json", "etf_gate_data_gaps_json TEXT")
        _ensure_column(conn, "target_allocations", "etf_gate_components_json", "etf_gate_components_json TEXT")


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, column_definition: str
) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)
