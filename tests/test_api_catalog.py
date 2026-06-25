from __future__ import annotations

from shadow_app.api_catalog import public_api_catalog


def test_public_api_catalog_lists_public_endpoints_once() -> None:
    catalog = public_api_catalog("http://127.0.0.1:8013/")

    assert catalog["system_name"] == "MyInvestShadow"
    assert catalog["version"] == "0.1.0"
    assert catalog["base_url"] == "http://127.0.0.1:8013"
    assert catalog["docs"] == {
        "swagger": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
    }
    assert catalog["total_endpoints"] == sum(
        len(group["endpoints"]) for group in catalog["groups"]
    )

    endpoints = [
        (endpoint["method"], endpoint["path"])
        for group in catalog["groups"]
        for endpoint in group["endpoints"]
    ]
    assert len(endpoints) == len(set(endpoints))
    assert ("GET", "/api") in endpoints
    assert ("POST", "/api/run/daily") in endpoints
    assert any(item["path"] == "/api/index" for item in catalog["recommended_entrypoints"])

    api_entry = next(endpoint for endpoint in _all_endpoints(catalog) if endpoint["path"] == "/api")
    refresh_entry = next(
        endpoint for endpoint in _all_endpoints(catalog) if endpoint["path"] == "/api/run/daily"
    )
    assert api_entry["read_only"] is True
    assert refresh_entry["read_only"] is False
    assert "不触发重计算" in "；".join(catalog["safety"])


def _all_endpoints(catalog: dict) -> list[dict]:
    return [
        endpoint
        for group in catalog["groups"]
        for endpoint in group["endpoints"]
    ]
