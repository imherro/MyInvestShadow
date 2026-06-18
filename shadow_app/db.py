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
    theme TEXT NOT NULL,
    stage TEXT NOT NULL,
    target_weight_ratio REAL NOT NULL,
    previous_weight_ratio REAL NOT NULL,
    drift_ratio REAL NOT NULL,
    price REAL,
    pct_chg REAL,
    evidence_score REAL,
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

CREATE TABLE IF NOT EXISTS actual_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_date TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
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
