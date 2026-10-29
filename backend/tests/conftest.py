from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_verificadores
from app.main import app
from app.services.health import VerificadorStore


@pytest.fixture
def com_stores() -> Callable[..., None]:
    """Injeta verificadores fake no /health (substitui os stores reais)."""

    def _aplicar(*verificadores: VerificadorStore) -> None:
        app.dependency_overrides[get_verificadores] = lambda: list(verificadores)

    return _aplicar


@pytest.fixture
async def cliente() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
