from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import ROOT_DIR, RuntimeConfig
from .db import init_db
from .service import (
    ensure_seed_data,
    latest_state,
    nav_curve,
    run_daily_rebalance,
    save_actual_holdings,
    source_status,
)

config = RuntimeConfig()
app = FastAPI(title="MyInvestShadow", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT_DIR / "static"), name="static")
templates = Jinja2Templates(directory=ROOT_DIR / "templates")

SHANGHAI = ZoneInfo("Asia/Shanghai")
_last_scheduled_day: str | None = None


class HoldingInput(BaseModel):
    code: str = Field(..., description="证券代码，例如 588170.SH")
    name: str | None = None
    weight_ratio: float = Field(..., ge=0, le=100, description="持仓权重百分比")
    theme: str | None = None


class ActualHoldingsInput(BaseModel):
    as_of_date: str | None = None
    source: str = "manual"
    holdings: list[HoldingInput]


async def _startup_seed() -> None:
    try:
        await asyncio.to_thread(ensure_seed_data)
    except Exception:
        # The UI and health endpoint expose empty state; a later manual refresh can recover.
        return


async def _scheduled_refresh_loop() -> None:
    global _last_scheduled_day
    await asyncio.sleep(3)
    await _startup_seed()
    while True:
        await asyncio.sleep(max(5, config.refresh_minutes) * 60)
        now = datetime.now(SHANGHAI)
        after_close = now.hour > 15 or (now.hour == 15 and now.minute >= 5)
        if not after_close:
            continue
        if _last_scheduled_day == now.date().isoformat():
            continue
        try:
            await asyncio.to_thread(run_daily_rebalance, "scheduled_close_refresh")
            _last_scheduled_day = now.date().isoformat()
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


@app.get("/api/state")
def api_state() -> dict[str, Any]:
    return latest_state()


@app.post("/api/run/daily")
def api_run_daily() -> dict[str, Any]:
    return run_daily_rebalance(reason="manual_api")


@app.get("/api/nav")
def api_nav() -> list[dict[str, Any]]:
    return nav_curve()


@app.get("/api/source-status")
def api_source_status() -> list[dict[str, Any]]:
    return source_status()


@app.get("/api/allocations/latest")
def api_allocations_latest() -> dict[str, Any]:
    state = latest_state()
    return {
        "run": state.get("run"),
        "allocations": state.get("allocations", []),
        "cash_ratio": (state.get("run") or {}).get("cash_ratio"),
    }


@app.post("/api/actual-holdings")
def api_actual_holdings(payload: ActualHoldingsInput) -> dict[str, Any]:
    holdings = [item.dict() for item in payload.holdings]
    return save_actual_holdings(holdings, payload.as_of_date, payload.source)


@app.get("/api/compare/latest")
def api_compare_latest() -> dict[str, Any]:
    state = latest_state()
    return {
        "run": state.get("run"),
        "actual_holdings": state.get("actual_holdings"),
        "comparison": state.get("comparison", []),
    }
