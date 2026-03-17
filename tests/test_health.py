"""Tests for the health endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from led_catcher.handlers.health import health_app, set_build_info


@pytest.fixture
def setup_build_info():
    set_build_info("1.0.0", "abc1234", "2024-01-15")


@pytest.mark.asyncio
async def test_healthz(setup_build_info):
    transport = ASGITransport(app=health_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"
    assert data["commit"] == "abc1234"
    assert "time" in data


@pytest.mark.asyncio
async def test_health_alias(setup_build_info):
    transport = ASGITransport(app=health_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
