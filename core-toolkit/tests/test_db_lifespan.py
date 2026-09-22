"""db_lifespanの統合テスト。"""

import pytest
from sqlalchemy import text
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.db_lifespan import (
    DbLifespanResource,
    get_db_connection,
    get_db_engine,
)
from core_toolkit.lifespan import create_lifespan


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_db_engine_returns_registered_engine():
    app = Starlette()
    lifespan = create_lifespan(
        DbLifespanResource("main", "sqlite+aiosqlite:///:memory:")
    )

    async with lifespan(app):
        engine = get_db_engine("main")(_make_request(app))
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1


def test_get_db_engine_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="main"):
        get_db_engine("main")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_engines_registered_independently():
    app = Starlette()
    lifespan = create_lifespan(
        DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"),
        DbLifespanResource("analytics", "sqlite+aiosqlite:///:memory:"),
    )

    async with lifespan(app):
        main_engine = get_db_engine("main")(_make_request(app))
        analytics_engine = get_db_engine("analytics")(_make_request(app))
        assert main_engine is not analytics_engine


@pytest.mark.asyncio
async def test_db_lifespan_resource_disposes_engine_on_exit():
    app = Starlette()
    lifespan = create_lifespan(
        DbLifespanResource("main", "sqlite+aiosqlite:///:memory:")
    )

    async with lifespan(app):
        engine = get_db_engine("main")(_make_request(app))

    with pytest.raises(Exception):
        async with engine.connect():
            pass


@pytest.mark.asyncio
async def test_get_db_connection_commits_on_success():
    app = Starlette()
    lifespan = create_lifespan(
        DbLifespanResource("main", "sqlite+aiosqlite:///:memory:")
    )

    async with lifespan(app):
        engine = get_db_engine("main")(_make_request(app))
        async with engine.begin() as setup_conn:
            await setup_conn.execute(
                text("CREATE TABLE items (id INTEGER PRIMARY KEY)")
            )

        gen = get_db_connection("main")(_make_request(app))
        conn = await gen.__anext__()
        await conn.execute(text("INSERT INTO items (id) VALUES (1)"))
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

        async with engine.connect() as check_conn:
            result = await check_conn.execute(text("SELECT COUNT(*) FROM items"))
            assert result.scalar() == 1


@pytest.mark.asyncio
async def test_get_db_connection_rolls_back_on_exception():
    app = Starlette()
    lifespan = create_lifespan(
        DbLifespanResource("main", "sqlite+aiosqlite:///:memory:")
    )

    async with lifespan(app):
        engine = get_db_engine("main")(_make_request(app))
        async with engine.begin() as setup_conn:
            await setup_conn.execute(
                text("CREATE TABLE items (id INTEGER PRIMARY KEY)")
            )

        gen = get_db_connection("main")(_make_request(app))
        conn = await gen.__anext__()
        await conn.execute(text("INSERT INTO items (id) VALUES (1)"))
        with pytest.raises(ValueError, match="boom"):
            await gen.athrow(ValueError("boom"))

        async with engine.connect() as check_conn:
            result = await check_conn.execute(text("SELECT COUNT(*) FROM items"))
            assert result.scalar() == 0
