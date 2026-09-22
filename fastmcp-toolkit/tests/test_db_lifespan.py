"""db_lifespan / CurrentDbEngine / CurrentDbConnection の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際にentry
するため、ツール関数からCurrentDbEngine()/CurrentDbConnection()がDepends経由
で正しく解決されることをend-to-endで検証する。
"""

import pytest
from fastmcp import Client, FastMCP
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from fastmcp_toolkit.db_lifespan import (
    CurrentDbConnection,
    CurrentDbEngine,
    db_lifespan,
)


@pytest.mark.asyncio
async def test_tool_resolves_db_connection_via_depends_and_commits():
    app = FastMCP("test", lifespan=db_lifespan("main", "sqlite+aiosqlite:///:memory:"))

    @app.tool
    async def setup(conn: AsyncConnection = CurrentDbConnection("main")) -> str:
        await conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))
        return "ok"

    @app.tool
    async def create_item(conn: AsyncConnection = CurrentDbConnection("main")) -> str:
        await conn.execute(text("INSERT INTO items (id) VALUES (1)"))
        return "ok"

    @app.tool
    async def count_items(conn: AsyncConnection = CurrentDbConnection("main")) -> int:
        result = await conn.execute(text("SELECT COUNT(*) FROM items"))
        return result.scalar() or 0

    async with Client(app) as client:
        await client.call_tool("setup", {})
        await client.call_tool("create_item", {})
        result = await client.call_tool("count_items", {})

    assert result.data == 1


@pytest.mark.asyncio
async def test_tool_rolls_back_when_tool_raises():
    app = FastMCP("test", lifespan=db_lifespan("main", "sqlite+aiosqlite:///:memory:"))

    @app.tool
    async def setup(conn: AsyncConnection = CurrentDbConnection("main")) -> str:
        await conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))
        return "ok"

    @app.tool
    async def create_item_fail(
        conn: AsyncConnection = CurrentDbConnection("main"),
    ) -> str:
        await conn.execute(text("INSERT INTO items (id) VALUES (1)"))
        raise ValueError("boom")

    @app.tool
    async def count_items(conn: AsyncConnection = CurrentDbConnection("main")) -> int:
        result = await conn.execute(text("SELECT COUNT(*) FROM items"))
        return result.scalar() or 0

    async with Client(app) as client:
        await client.call_tool("setup", {})
        with pytest.raises(Exception, match="boom"):
            await client.call_tool("create_item_fail", {})
        result = await client.call_tool("count_items", {})

    assert result.data == 0


@pytest.mark.asyncio
async def test_tool_resolves_db_engine_via_depends():
    app = FastMCP("test", lifespan=db_lifespan("main", "sqlite+aiosqlite:///:memory:"))

    @app.tool
    async def driver_name(engine: AsyncEngine = CurrentDbEngine("main")) -> str:
        return engine.dialect.driver

    async with Client(app) as client:
        result = await client.call_tool("driver_name", {})

    assert result.data == "aiosqlite"


@pytest.mark.asyncio
async def test_multiple_named_engines_are_independent():
    app = FastMCP(
        "test",
        lifespan=db_lifespan("main", "sqlite+aiosqlite:///:memory:")
        | db_lifespan("analytics", "sqlite+aiosqlite:///:memory:"),
    )

    @app.tool
    async def compare(
        main: AsyncEngine = CurrentDbEngine("main"),
        analytics: AsyncEngine = CurrentDbEngine("analytics"),
    ) -> bool:
        return main is not analytics

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(engine: AsyncEngine = CurrentDbEngine("main")) -> str:
        return type(engine).__name__

    async with Client(app) as client:
        with pytest.raises(Exception, match="engine"):
            await client.call_tool("whoami", {})
