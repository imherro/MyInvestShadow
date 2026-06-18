from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .config import get_tushare_token


@dataclass(frozen=True)
class PricePoint:
    code: str
    close: float | None
    pct_chg: float | None
    source: str
    error: str | None = None


MARKET_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trade_date_compact(basis_date: str) -> str:
    return basis_date.replace("-", "")


def _fetch_tushare_one(pro: Any, code: str, trade_date: str) -> PricePoint:
    methods = ("fund_daily", "daily") if code.endswith((".SH", ".SZ", ".BJ")) else ("daily",)
    last_error: str | None = None
    for method_name in methods:
        try:
            method = getattr(pro, method_name)
            df = method(ts_code=code, trade_date=trade_date)
            if df is None or df.empty:
                continue
            row = df.iloc[0]
            return PricePoint(
                code=code,
                close=_safe_float(row.get("close")),
                pct_chg=_safe_float(row.get("pct_chg")),
                source=f"Tushare.{method_name}",
            )
        except Exception as exc:  # pragma: no cover - depends on local Tushare state
            last_error = f"{type(exc).__name__}: {exc}"
    return PricePoint(code=code, close=None, pct_chg=None, source="Tushare", error=last_error)


def fetch_tushare_prices(codes: list[str], basis_date: str) -> dict[str, PricePoint]:
    synthetic = {
        code: PricePoint(
            code=code,
            close=None,
            pct_chg=0.0 if code == "DEFENSIVE.CASH" else None,
            source="synthetic",
            error=None,
        )
        for code in codes
        if not MARKET_CODE_RE.match(code)
    }
    market_codes = [code for code in codes if MARKET_CODE_RE.match(code)]
    if not market_codes:
        return synthetic
    token = get_tushare_token()
    if not token:
        return synthetic | {
            code: PricePoint(code=code, close=None, pct_chg=None, source="unavailable", error="missing Tushare token")
            for code in market_codes
        }
    try:
        import tushare as ts

        ts.set_token(token)
        pro = ts.pro_api(token)
    except Exception as exc:  # pragma: no cover - depends on installed runtime
        return synthetic | {
            code: PricePoint(
                code=code,
                close=None,
                pct_chg=None,
                source="unavailable",
                error=f"{type(exc).__name__}: {exc}",
            )
            for code in market_codes
        }

    trade_date = _trade_date_compact(basis_date)
    return synthetic | {code: _fetch_tushare_one(pro, code, trade_date) for code in market_codes}


def theme_price_fallback(theme_payload: dict[str, Any]) -> dict[str, PricePoint]:
    latest = theme_payload.get("latest_result") or {}
    result: dict[str, PricePoint] = {}
    for row in latest.get("etf_top") or []:
        code = row.get("code")
        if not code:
            continue
        result[code] = PricePoint(
            code=code,
            close=_safe_float(row.get("close")),
            pct_chg=_safe_float(row.get("r1")),
            source="theme.latest_result.etf_top",
        )
    return result


def merge_price_maps(
    tushare_prices: dict[str, PricePoint], fallback_prices: dict[str, PricePoint]
) -> dict[str, PricePoint]:
    merged = dict(fallback_prices)
    for code, point in tushare_prices.items():
        if point.close is not None or point.pct_chg is not None:
            merged[code] = point
        elif code not in merged:
            merged[code] = point
    return merged
