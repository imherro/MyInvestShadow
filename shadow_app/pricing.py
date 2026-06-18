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
    amount: float | None = None
    r5: float | None = None
    r20: float | None = None
    source_score: float | None = None
    amount_rank: float | None = None
    r1_rank: float | None = None
    r5_rank: float | None = None
    r20_rank: float | None = None
    unit_nav: float | None = None
    premium_rate: float | None = None


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


def _fetch_fund_nav(pro: Any, code: str, trade_date: str) -> float | None:
    try:
        df = pro.fund_nav(ts_code=code, end_date=trade_date)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    nav_date = str(row.get("end_date") or row.get("nav_date") or "")
    if nav_date and nav_date.replace("-", "") != trade_date:
        return None
    return _safe_float(row.get("unit_nav"))


def _premium_rate(close: float | None, unit_nav: float | None) -> float | None:
    if close is None or unit_nav is None or unit_nav <= 0:
        return None
    return ((close / unit_nav) - 1.0) * 100.0


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
            close = _safe_float(row.get("close"))
            unit_nav = _fetch_fund_nav(pro, code, trade_date) if method_name == "fund_daily" else None
            return PricePoint(
                code=code,
                close=close,
                pct_chg=_safe_float(row.get("pct_chg")),
                source=f"Tushare.{method_name}" + ("+fund_nav" if unit_nav is not None else ""),
                amount=_safe_float(row.get("amount")),
                unit_nav=unit_nav,
                premium_rate=_premium_rate(close, unit_nav),
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
            amount=_safe_float(row.get("amount")),
            r5=_safe_float(row.get("r5")),
            r20=_safe_float(row.get("r20")),
            source_score=_safe_float(row.get("score")),
            amount_rank=_safe_float(row.get("amount_rank")),
            r1_rank=_safe_float(row.get("r1_rank")),
            r5_rank=_safe_float(row.get("r5_rank")),
            r20_rank=_safe_float(row.get("r20_rank")),
        )
    return result


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _merge_point(primary: PricePoint, fallback: PricePoint | None) -> PricePoint:
    if fallback is None:
        return primary
    sources = [primary.source]
    if fallback.source and fallback.source not in sources:
        sources.append(fallback.source)
    return PricePoint(
        code=primary.code,
        close=_coalesce(primary.close, fallback.close),
        pct_chg=_coalesce(primary.pct_chg, fallback.pct_chg),
        source="+".join(sources),
        error=primary.error or fallback.error,
        amount=_coalesce(primary.amount, fallback.amount),
        r5=_coalesce(primary.r5, fallback.r5),
        r20=_coalesce(primary.r20, fallback.r20),
        source_score=_coalesce(primary.source_score, fallback.source_score),
        amount_rank=_coalesce(primary.amount_rank, fallback.amount_rank),
        r1_rank=_coalesce(primary.r1_rank, fallback.r1_rank),
        r5_rank=_coalesce(primary.r5_rank, fallback.r5_rank),
        r20_rank=_coalesce(primary.r20_rank, fallback.r20_rank),
        unit_nav=_coalesce(primary.unit_nav, fallback.unit_nav),
        premium_rate=_coalesce(primary.premium_rate, fallback.premium_rate),
    )


def merge_price_maps(
    tushare_prices: dict[str, PricePoint], fallback_prices: dict[str, PricePoint]
) -> dict[str, PricePoint]:
    merged = dict(fallback_prices)
    for code, point in tushare_prices.items():
        if point.close is not None or point.pct_chg is not None:
            merged[code] = _merge_point(point, fallback_prices.get(code))
        elif code not in merged:
            merged[code] = point
    return merged
