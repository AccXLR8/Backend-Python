from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_allowed_origin_receives_cors_header_on_auth_error():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/users/me",
            headers={"Origin": "https://superkritical.com"},
        )

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "https://superkritical.com"


@pytest.mark.asyncio
async def test_disallowed_origin_does_not_receive_cors_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/users/me",
            headers={"Origin": "https://untrusted.example"},
        )

    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers
