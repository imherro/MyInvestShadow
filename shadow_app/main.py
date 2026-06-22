from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import ROOT_DIR, RuntimeConfig
from .db import init_db
from .service import (
    ensure_seed_data,
    index_state,
    latest_state,
    nav_curve,
    rebalance_history,
    run_daily_rebalance,
    source_status,
)

config = RuntimeConfig()
app = FastAPI(title="MyInvestShadow", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT_DIR / "static"), name="static")
templates = Jinja2Templates(directory=ROOT_DIR / "templates")

SHANGHAI = ZoneInfo("Asia/Shanghai")
_successful_scheduled_day: str | None = None
_attempted_schedule_slots: set[str] = set()


def _schedule_slot_key(
    now: datetime,
    successful_day: str | None,
    attempted_slots: set[str],
    schedule_times: tuple[str, ...],
) -> str | None:
    day = now.date().isoformat()
    if successful_day == day:
        return None

    due_slot: str | None = None
    for item in schedule_times:
        try:
            hour_text, minute_text = item.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except ValueError:
            continue
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= slot_time:
            due_slot = f"{hour:02d}:{minute:02d}"

    if due_slot is None:
        return None

    key = f"{day}:{due_slot}"
    if key in attempted_slots:
        return None
    return key


async def _startup_seed() -> None:
    try:
        await asyncio.to_thread(ensure_seed_data)
    except Exception:
        # The UI and health endpoint expose empty state; a later manual refresh can recover.
        return


async def _scheduled_refresh_loop() -> None:
    global _successful_scheduled_day
    await asyncio.sleep(3)
    await _startup_seed()
    while True:
        await asyncio.sleep(max(1, config.refresh_minutes) * 60)
        now = datetime.now(SHANGHAI)
        day = now.date().isoformat()
        for key in list(_attempted_schedule_slots):
            if not key.startswith(f"{day}:"):
                _attempted_schedule_slots.discard(key)

        slot_key = _schedule_slot_key(
            now,
            _successful_scheduled_day,
            _attempted_schedule_slots,
            config.schedule_times,
        )
        if not slot_key:
            continue
        _attempted_schedule_slots.add(slot_key)
        slot = slot_key.split(":", 1)[1]
        try:
            await asyncio.to_thread(run_daily_rebalance, f"scheduled_evening_refresh_{slot}")
            _successful_scheduled_day = day
        except Exception:
            continue


@app.on_event("startup")
async def startup() -> None:
    init_db()
    asyncio.create_task(_scheduled_refresh_loop())


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    state = latest_state()
    return {
        "ok": True,
        "has_run": bool(state.get("run")),
        "latest_basis_date": (state.get("run") or {}).get("basis_date"),
        "port": config.port,
    }


@app.get("/api/latest")
def api_latest() -> dict[str, Any]:
    return latest_state()


@app.get("/api/index")
def api_index() -> dict[str, Any]:
    return index_state()


@app.post("/api/run/daily")
def api_run_daily() -> dict[str, Any]:
    return run_daily_rebalance(reason="manual_api")


@app.get("/api/nav")
def api_nav() -> list[dict[str, Any]]:
    return nav_curve()


@app.get("/api/rebalance-history")
def api_rebalance_history() -> list[dict[str, Any]]:
    return rebalance_history()


@app.get("/api/source-status")
def api_source_status() -> list[dict[str, Any]]:
    return source_status()


@app.get("/api/allocations/latest")
def api_allocations_latest() -> dict[str, Any]:
    state = latest_state()
    return {
        "run": state.get("run"),
        "allocations": state.get("allocations", []),
        "sleeve_summary": state.get("sleeve_summary", {}),
        "cash_ratio": (state.get("run") or {}).get("cash_ratio"),
    }
