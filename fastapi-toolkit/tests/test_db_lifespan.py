"""DbLifespanResource/get_db_connection/get_db_engineがDepends経由で
正しく解決されることを検証する統合テスト。

DbLifespanResource自体の起動・終了処理とget_db_connectionのcommit/rollback
ロジックはcore-toolkit側（core_toolkit/tests/test_db_lifespan.py）でテスト済み
のため、ここではFastAPI固有の部分――``Annotated[T, Depends(...)]``が実際の
エンドポイントで解決されること――だけを検証する。
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from fastapi_toolkit.db_lifespan import DbLifespanResource, get_db_connection, get_db_engine
from fastapi_toolkit.lifespan import create_lifespan

MainDbConnection = Annotated[AsyncConnection, Depends(get_db_connection("main"))]
MainDbEngine = Annotated[AsyncEngine, Depends(get_db_engine("main"))]


@pytest.mark.asyncio
async def test_db_connection_resolves_via_depends_and_commits():
    app = FastAPI()

    @app.post("/items")
    async def create_item(conn: MainDbConnection) -> dict[str, bool]:
        await conn.execute(text("INSERT INTO items (id) VALUES (1)"))
        return {"ok": True}

    lifespan = create_lifespan(DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"))
    async with lifespan(app):
        engine = app.state.db_engines["main"]
        async with engine.begin() as setup_conn:
            await setup_conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/items")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        async with engine.connect() as check_conn:
            result = await check_conn.execute(text("SELECT COUNT(*) FROM items"))
            assert result.scalar() == 1


@pytest.mark.asyncio
async def test_db_connection_rolls_back_when_endpoint_raises():
    app = FastAPI()

    @app.post("/items/fail")
    async def create_item_fail(conn: MainDbConnection) -> dict[str, bool]:
        await conn.execute(text("INSERT INTO items (id) VALUES (1)"))
        raise ValueError("boom")

    lifespan = create_lifespan(DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"))
    async with lifespan(app):
        engine = app.state.db_engines["main"]
        async with engine.begin() as setup_conn:
            await setup_conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True), base_url="http://test"
        ) as client:
            with pytest.raises(ValueError, match="boom"):
                await client.post("/items/fail")

        async with engine.connect() as check_conn:
            result = await check_conn.execute(text("SELECT COUNT(*) FROM items"))
            assert result.scalar() == 0


@pytest.mark.asyncio
async def test_db_engine_resolves_via_depends():
    app = FastAPI()

    @app.get("/health")
    async def health(engine: MainDbEngine) -> dict[str, str]:
        return {"driver": engine.dialect.driver}

    lifespan = create_lifespan(DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"driver": "aiosqlite"}
