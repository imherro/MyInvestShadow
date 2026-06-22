from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from shadow_app import main
from shadow_app.config import RuntimeConfig


def test_mutation_access_allows_loopback_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "config", RuntimeConfig(host="127.0.0.1", api_token=None))

    main._validate_mutation_access(None)


def test_mutation_access_rejects_external_host_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "config", RuntimeConfig(host="0.0.0.0", api_token=None))

    with pytest.raises(HTTPException) as excinfo:
        main._validate_mutation_access(None)

    assert excinfo.value.status_code == 403


def test_mutation_access_requires_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "config", RuntimeConfig(host="0.0.0.0", api_token="secret"))

    with pytest.raises(HTTPException) as excinfo:
        main._validate_mutation_access(None)
    assert excinfo.value.status_code == 403

    main._validate_mutation_access("secret")


def test_manual_refresh_rejects_when_refresh_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LockedRefresh:
        def locked(self) -> bool:
            return True

    monkeypatch.setattr(main, "config", RuntimeConfig(host="127.0.0.1", api_token=None))
    monkeypatch.setattr(main, "_refresh_lock", LockedRefresh())

    async def call_endpoint() -> None:
        with pytest.raises(HTTPException) as excinfo:
            await main.api_run_daily()
        assert excinfo.value.status_code == 409

    asyncio.run(call_endpoint())
