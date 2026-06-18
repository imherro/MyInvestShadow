from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "shadow_account.sqlite"
ENV_PATH = ROOT_DIR / ".env"

MARKET_API_URL = os.getenv(
    "MARKET_API_URL", "https://market.okbbc.com//api/research/latest"
)
THEME_API_URL = os.getenv(
    "THEME_API_URL", "https://theme.okbbc.com/api/shadow-account/latest"
)

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 MyInvestShadow/0.1 (+https://github.com/imherro/MyInvestShadow)",
    "Accept": "application/json,text/plain,*/*",
}


@dataclass(frozen=True)
class RuntimeConfig:
    host: str = os.getenv("SHADOW_HOST", "127.0.0.1")
    port: int = int(os.getenv("SHADOW_PORT", "8013"))
    refresh_minutes: int = int(os.getenv("SHADOW_REFRESH_MINUTES", "30"))
    db_path: Path = DB_PATH


def load_local_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_tushare_token() -> str | None:
    load_local_env()
    for key in ("TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN", "tushare_token"):
        value = os.getenv(key)
        if value:
            return value
    return None
