from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shadow_app.config import RuntimeConfig


if __name__ == "__main__":
    config = RuntimeConfig()
    uvicorn.run("shadow_app.main:app", host=config.host, port=config.port, reload=False)
