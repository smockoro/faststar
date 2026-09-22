# FastMCP向けDB接続部品 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** core-toolkit / fastapi-toolkit / fastmcp-toolkitに、SQLAlchemy(async)経由でSQLite/Postgres/MySQLに対応するDB接続lifespan管理部品（`DbLifespanResource`/`get_db_engine`/`get_db_connection`とFastMCP向けの`db_lifespan`/`CurrentDbEngine`/`CurrentDbConnection`）を追加する。

**Architecture:** 既存の`RedisLifespanResource`と同じ3層構造（core-toolkitに実体、fastapi-toolkitは薄い再エクスポート、fastmcp-toolkitは`lifespan_context`ベースの独立実装）を踏襲する。DBは複数エンジンを名前付きで同時登録できる点と、呼び出し単位でbegin/commit/rollback/closeするyieldベースのConnection providerを持つ点がRedisにはなかった新規要素。

**Tech Stack:** SQLAlchemy 2.0(`sqlalchemy.ext.asyncio`) + `aiosqlite`（テストは全てこれで完結）。Starlette/FastAPI/FastMCP/`uncalled_for`は既存依存をそのまま利用。

**Spec:** `docs/superpowers/specs/2026-09-22-fastmcp-db-connection-design.md`

## Global Constraints

- Python 3.14+（既存パッケージのrequires-pythonに合わせる）
- ruffでE, F, I, UPルールをlint。core-toolkit/fastmcp-toolkitは既定line-length、fastapi-toolkitは`line-length = 120`
- 公開API（`DbLifespanResource`, `get_db_engine`, `get_db_connection`, `db_lifespan`, `CurrentDbEngine`, `CurrentDbConnection`）には日本語・GoogleスタイルのDocstringを付ける
- SQLAlchemyは**Core**のみ使用し、ORM機能（宣言的モデル/Session/relationship）は使わない
- 入れ子トランザクション（SAVEPOINT）・独立トランザクション（REQUIRES_NEW相当）・Repository/Usecase/UnitOfWorkパターン・DuckDB対応・ヘルスチェック統合は本計画のスコープ外（spec「非スコープ」節参照）
- テストは`sqlite+aiosqlite:///:memory:`のみを使い、Postgres/MySQLの実サーバーには接続しない
- 依存追加後は必ず`uv sync --all-extras`を実行してから実装・テストに進む

---

## Task 1: core-toolkit — `DbLifespanResource`/`get_db_engine`/`get_db_connection`

**Files:**
- Modify: `core-toolkit/pyproject.toml`
- Create: `core-toolkit/src/core_toolkit/db_lifespan.py`
- Test: `core-toolkit/tests/test_db_lifespan.py`

**Interfaces:**
- Consumes: `core_toolkit.lifespan.LifespanResource`（既存、`context(self, app: Starlette) -> AbstractAsyncContextManager`を実装するABC）、`core_toolkit.lifespan.create_lifespan(*resources: LifespanResource) -> Callable[..., AsyncGenerator[None, Any]]`（既存）
- Produces:
  - `DbLifespanResource(name: str, url: str, **engine_kwargs: Any)` — `LifespanResource`のサブクラス
  - `get_db_engine(name: str) -> Callable[[Request], AsyncEngine]`
  - `get_db_connection(name: str) -> Callable[[Request], AsyncGenerator[AsyncConnection, None]]`
  - これらはTask 2（fastapi-toolkit）がそのまま再エクスポートする

- [ ] **Step 1: pyproject.tomlにDB種別ごとのextraを追加**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis = [...]`の直後へ以下を追加する。

```toml
sqlite = [
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
]
postgres = [
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
]
mysql = [
    "sqlalchemy[asyncio]>=2.0",
    "asyncmy>=0.2",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd core-toolkit && uv sync --all-extras`
Expected: `sqlalchemy`/`aiosqlite`/`asyncpg`/`asyncmy`が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`core-toolkit/tests/test_db_lifespan.py`を作成する。

```python
"""db_lifespanの統合テスト。"""

import pytest
from sqlalchemy import text
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.db_lifespan import DbLifespanResource, get_db_connection, get_db_engine
from core_toolkit.lifespan import create_lifespan


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_db_engine_returns_registered_engine():
    app = Starlette()
    lifespan = create_lifespan(DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"))

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
    lifespan = create_lifespan(DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"))

    async with lifespan(app):
        engine = get_db_engine("main")(_make_request(app))

    with pytest.raises(Exception):
        async with engine.connect():
            pass


@pytest.mark.asyncio
async def test_get_db_connection_commits_on_success():
    app = Starlette()
    lifespan = create_lifespan(DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"))

    async with lifespan(app):
        engine = get_db_engine("main")(_make_request(app))
        async with engine.begin() as setup_conn:
            await setup_conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))

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
    lifespan = create_lifespan(DbLifespanResource("main", "sqlite+aiosqlite:///:memory:"))

    async with lifespan(app):
        engine = get_db_engine("main")(_make_request(app))
        async with engine.begin() as setup_conn:
            await setup_conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY)"))

        gen = get_db_connection("main")(_make_request(app))
        conn = await gen.__anext__()
        await conn.execute(text("INSERT INTO items (id) VALUES (1)"))
        with pytest.raises(ValueError, match="boom"):
            await gen.athrow(ValueError("boom"))

        async with engine.connect() as check_conn:
            result = await check_conn.execute(text("SELECT COUNT(*) FROM items"))
            assert result.scalar() == 0
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd core-toolkit && uv run pytest tests/test_db_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core_toolkit.db_lifespan'`）

- [ ] **Step 5: 実装を書く**

`core-toolkit/src/core_toolkit/db_lifespan.py`を作成する。

```python
"""Starlette/ASGIアプリ向けDB接続lifespan管理。

SQLAlchemy(async)経由でSQLite/Postgres/MySQL等の複数バックエンドに対応する。
名前付きで複数エンジンを同時に登録・取得できる。

利用には ``core-toolkit[sqlite]``/``core-toolkit[postgres]``/
``core-toolkit[mysql]`` のいずれかのextraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class DbLifespanResource(LifespanResource):
    """名前付きでAsyncEngineを登録するリソース。同じappに複数登録できる。

    Args:
        name: このエンジンを識別する名前（例: ``"main"``）。
            ``get_db_engine``/``get_db_connection`` で同じ名前を指定して取得する。
        url: 接続先のURL（例: ``sqlite+aiosqlite:///./app.db``）。
        engine_kwargs: ``create_async_engine`` にそのまま渡す追加引数。
    """

    def __init__(self, name: str, url: str, **engine_kwargs: Any) -> None:
        self._name = name
        self._url = url
        self._engine_kwargs = engine_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        engine = create_async_engine(self._url, **self._engine_kwargs)
        engines: dict[str, AsyncEngine] = getattr(app.state, "db_engines", {})
        app.state.db_engines = {**engines, self._name: engine}

        try:
            yield engine
        finally:
            await engine.dispose()


def get_db_engine(name: str) -> Callable[[Request], AsyncEngine]:
    """名前を指定してAsyncEngineを取得するprovider関数を生成する（生アクセス用）。

    マイグレーション・ヘルスチェック・独立トランザクション（REQUIRES_NEW相当）
    など、トランザクション境界をアプリ側で制御したい場合に使う。

    Args:
        name: ``DbLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``DbLifespanResource`` が登録されていない場合は ``RuntimeError`` を送出する。
    """

    def get_engine(request: Request) -> AsyncEngine:
        engines: dict[str, AsyncEngine] = getattr(request.app.state, "db_engines", {})
        if name not in engines:
            raise RuntimeError(
                f"db_engines['{name}'] is not set. "
                f"Did you forget to register DbLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return engines[name]

    get_engine.__name__ = f"get_db_engine_{name}"
    return get_engine


def get_db_connection(
    name: str,
) -> Callable[[Request], AsyncGenerator[AsyncConnection, None]]:
    """名前を指定して呼び出し単位のConnectionを取得するprovider関数を生成する。

    Depends解決のライフタイム＝1トランザクションとして扱う。正常終了でcommit、
    例外でrollback、いずれの場合も必ずclose。入れ子トランザクション
    （SAVEPOINT）が必要な場合は、取得した ``AsyncConnection`` に対して
    呼び出し側が ``begin_nested()`` を直接呼ぶ。

    Args:
        name: ``DbLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取る非同期ジェネレータ型のprovider関数。
    """

    async def get_connection(request: Request) -> AsyncGenerator[AsyncConnection, None]:
        engine = get_db_engine(name)(request)
        async with engine.begin() as conn:
            yield conn

    get_connection.__name__ = f"get_db_connection_{name}"
    return get_connection
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd core-toolkit && uv run pytest tests/test_db_lifespan.py -v`
Expected: PASS（6件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd core-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/uv.lock core-toolkit/src/core_toolkit/db_lifespan.py core-toolkit/tests/test_db_lifespan.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add named multi-engine DB connection lifespan

SQLAlchemy(async)経由でSQLite/Postgres/MySQLに対応するDbLifespanResource/
get_db_engine/get_db_connectionを追加。呼び出し単位でbegin/commit/rollback/
closeするConnection providerと、名前付き複数エンジン登録に対応する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: fastapi-toolkit — `db_lifespan.py` 薄いラッパー

**Files:**
- Modify: `fastapi-toolkit/pyproject.toml`
- Create: `fastapi-toolkit/src/fastapi_toolkit/db_lifespan.py`
- Test: `fastapi-toolkit/tests/test_db_lifespan.py`

**Interfaces:**
- Consumes: Task 1の`core_toolkit.db_lifespan.DbLifespanResource`/`get_db_engine`/`get_db_connection`（シグネチャは Task 1 の Produces を参照）、`fastapi_toolkit.lifespan.create_lifespan`（既存）
- Produces: `fastapi_toolkit.db_lifespan.DbLifespanResource`/`get_db_engine`/`get_db_connection`（Task 1のものをそのまま再エクスポート、シグネチャ変更なし）

- [ ] **Step 1: pyproject.tomlにDB種別ごとのextraを追加**

`fastapi-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis = [...]`の直後へ以下を追加する（`redis`エクストラが`"core-toolkit[redis]"`を参照している既存パターンに合わせる）。

```toml
sqlite = [
  "core-toolkit[sqlite]",
]
postgres = [
  "core-toolkit[postgres]",
]
mysql = [
  "core-toolkit[mysql]",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd fastapi-toolkit && uv sync --all-extras`
Expected: core-toolkitの`sqlite`/`postgres`/`mysql`エクストラ経由で`sqlalchemy`/`aiosqlite`等が解決される。

- [ ] **Step 3: 失敗するテストを書く**

`fastapi-toolkit/tests/test_db_lifespan.py`を作成する。

```python
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
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_db_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastapi_toolkit.db_lifespan'`）

- [ ] **Step 5: 実装を書く**

`fastapi-toolkit/src/fastapi_toolkit/db_lifespan.py`を作成する。

```python
"""DB接続lifespanリソースのFastAPI向け薄いラッパー。

``DbLifespanResource``/``get_db_engine``/``get_db_connection`` はFastAPIに
依存せず ``core_toolkit.db_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[AsyncConnection, Depends(get_db_connection("main"))]``）は
アプリ側で名前を指定して定義する。

利用には ``fastapi-toolkit[sqlite]``/``fastapi-toolkit[postgres]``/
``fastapi-toolkit[mysql]`` のいずれかのextraのインストールが必要。
"""

from core_toolkit.db_lifespan import DbLifespanResource, get_db_connection, get_db_engine

__all__ = ["DbLifespanResource", "get_db_connection", "get_db_engine"]
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_db_lifespan.py -v`
Expected: PASS（3件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd fastapi-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add fastapi-toolkit/pyproject.toml fastapi-toolkit/uv.lock fastapi-toolkit/src/fastapi_toolkit/db_lifespan.py fastapi-toolkit/tests/test_db_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastapi-toolkit): re-export DB connection lifespan via Depends

core_toolkit.db_lifespanのDbLifespanResource/get_db_engine/
get_db_connectionをFastAPIのDependsでエンドポイントから解決できるよう
再エクスポートする。名前ごとの型エイリアスはアプリ側で定義する運用とする。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: fastmcp-toolkit — `db_lifespan.py` 独立実装

**Files:**
- Modify: `fastmcp-toolkit/pyproject.toml`
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/db_lifespan.py`
- Test: `fastmcp-toolkit/tests/test_db_lifespan.py`

**Interfaces:**
- Consumes: `fastmcp.server.lifespan.lifespan`/`Lifespan`、`fastmcp.server.dependencies.CurrentContext`、`uncalled_for.Depends`（いずれも既存、`fastmcp_toolkit/redis_lifespan.py`と同じ使い方）
- Produces:
  - `db_lifespan(name: str, url: str, **engine_kwargs: Any) -> Lifespan`
  - `CurrentDbEngine(name: str) -> AsyncEngine`
  - `CurrentDbConnection(name: str) -> AsyncConnection`

**重要な実装上の注意（uncalled_for.Depends特有の制約）:** `uncalled_for.Depends`はFastAPIと異なり、factory関数が**非同期ジェネレータであること自体を検出してyieldベースの後始末をしない**。`uncalled_for`内部の`_resolve_factory_value`はfactoryの**戻り値の型**（`isinstance(raw_value, AbstractAsyncContextManager)`かどうか）だけを見る。そのため`CurrentDbConnection`のfactoryは、`async def ...: yield conn`ではなく、**同期関数**が`engine.begin()`（呼び出すだけでまだenterしていない`AbstractAsyncContextManager`）を**そのまま返す**形にする。これは実装時に検証済み（`engine.begin()`は`isinstance(..., AbstractAsyncContextManager)`が`True`になることを確認済み）。

- [ ] **Step 1: pyproject.tomlにDB種別ごとのextraを追加**

`fastmcp-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis = [...]`の直後へ以下を追加する（fastmcp-toolkitの`redis`エクストラが自己完結している既存パターンに合わせる。`db_lifespan.py`はcore-toolkitを再利用せず独立実装のため）。

```toml
sqlite = [
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
]
postgres = [
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
]
mysql = [
    "sqlalchemy[asyncio]>=2.0",
    "asyncmy>=0.2",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd fastmcp-toolkit && uv sync --all-extras`
Expected: `sqlalchemy`/`aiosqlite`/`asyncpg`/`asyncmy`が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_db_lifespan.py`を作成する。

```python
"""db_lifespan / CurrentDbEngine / CurrentDbConnection の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際にentry
するため、ツール関数からCurrentDbEngine()/CurrentDbConnection()がDepends経由
で正しく解決されることをend-to-endで検証する。
"""

import pytest
from fastmcp import Client, FastMCP
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from fastmcp_toolkit.db_lifespan import CurrentDbConnection, CurrentDbEngine, db_lifespan


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
    async def create_item_fail(conn: AsyncConnection = CurrentDbConnection("main")) -> str:
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
        with pytest.raises(Exception, match="main"):
            await client.call_tool("whoami", {})
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_db_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastmcp_toolkit.db_lifespan'`）

- [ ] **Step 5: 実装を書く**

`fastmcp-toolkit/src/fastmcp_toolkit/db_lifespan.py`を作成する。

```python
"""FastMCPサーバー向けDB接続lifespan統合。

Starlette向けの ``core_toolkit.db_lifespan.DbLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、ここでは独立した実装を
持つ。DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せ、
``fastapi_toolkit`` の ``Depends`` チェーンと同じ書き味をツール関数側にも
提供する。名前付きで複数エンジンを同時に利用でき、``|`` 演算子で他のlifespan
と合成できる。

利用には ``fastmcp-toolkit[sqlite]``/``fastmcp-toolkit[postgres]``/
``fastmcp-toolkit[mysql]`` のいずれかのextraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"db_engine:{name}"


def db_lifespan(name: str, url: str, **engine_kwargs: Any) -> Lifespan:
    """AsyncEngineのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``sqlalchemy.ext.asyncio`` のAsyncEngineを生成してlifespan_context
    に格納し、終了時にdisposeする。``FastMCP(lifespan=db_lifespan(name, url))``
    として使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のDBを同時に登録できる。

    Args:
        name: このエンジンを識別する名前。``CurrentDbEngine``/
            ``CurrentDbConnection`` で同じ名前を指定して取得する。
        url: 接続先のURL（例: ``sqlite+aiosqlite:///./app.db``）。
        engine_kwargs: ``create_async_engine`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _db_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        engine = create_async_engine(url, **engine_kwargs)
        try:
            yield {_lifespan_key(name): engine}
        finally:
            await engine.dispose()

    return _db_lifespan


def _get_db_engine(name: str) -> Callable[[Context], AsyncEngine]:
    def get_engine(ctx: Context = CurrentContext()) -> AsyncEngine:
        engine = ctx.lifespan_context.get(_lifespan_key(name))
        if engine is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=db_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(AsyncEngine, engine)

    get_engine.__name__ = f"get_db_engine_{name}"
    return get_engine


def _get_db_connection(
    name: str,
) -> Callable[[Context], AbstractAsyncContextManager[AsyncConnection]]:
    # uncalled_for.Dependsはfactoryの戻り値がAbstractAsyncContextManagerかどうか
    # で後始末の要否を判定する（非同期ジェネレータ関数であること自体は見ない）。
    # そのためengine.begin()（未enterのcontext manager）をそのまま返す。
    def get_connection(
        ctx: Context = CurrentContext(),
    ) -> AbstractAsyncContextManager[AsyncConnection]:
        engine = _get_db_engine(name)(ctx)
        return engine.begin()

    get_connection.__name__ = f"get_db_connection_{name}"
    return get_connection


def CurrentDbEngine(name: str) -> AsyncEngine:  # noqa: N802
    """``db_lifespan(name, ...)`` が生成したAsyncEngineを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(engine: AsyncEngine = CurrentDbEngine("main")) -> str:
            ...

    Args:
        name: ``db_lifespan(name=...)`` に登録した名前。

    Returns:
        AsyncEngine: ``Depends(...)`` でラップされた、実行時に解決されるEngine。
    """
    return cast(AsyncEngine, Depends(_get_db_engine(name)))


def CurrentDbConnection(name: str) -> AsyncConnection:  # noqa: N802
    """``db_lifespan(name, ...)`` のEngineから呼び出し単位のConnectionを取得する
    Depends。

    ツール呼び出し1回＝1トランザクションとして扱う。正常終了でcommit、
    例外でrollback、いずれの場合も必ずclose。

    Example::

        @app.tool
        async def my_tool(conn: AsyncConnection = CurrentDbConnection("main")) -> str:
            ...

    Args:
        name: ``db_lifespan(name=...)`` に登録した名前。

    Returns:
        AsyncConnection: ``Depends(...)`` でラップされた、実行時に解決される
        トランザクション付きConnection。
    """
    return cast(AsyncConnection, Depends(_get_db_connection(name)))
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_db_lifespan.py -v`
Expected: PASS（5件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd fastmcp-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add fastmcp-toolkit/pyproject.toml fastmcp-toolkit/uv.lock fastmcp-toolkit/src/fastmcp_toolkit/db_lifespan.py fastmcp-toolkit/tests/test_db_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastmcp-toolkit): add named multi-engine DB connection lifespan

lifespan_contextベースの独立実装でdb_lifespan/CurrentDbEngine/
CurrentDbConnectionを追加。uncalled_for.Dependsは戻り値の型でしか
後始末要否を判定しないため、CurrentDbConnectionはengine.begin()を
そのまま返す形で実装した。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 自己レビューで確認した点

- **spec網羅性**: spec「意思決定サマリー」の6項目（抽象化手段/実行モデル/トランザクション境界/複数DBエンジン/extras構成/テスト方針）は全タスクのコード・extras・テストに反映済み。「非スコープ」節の各項目（Repository/Usecase例・入れ子/REQUIRES_NEW API・マイグレーション・生ドライバー・ヘルスチェック統合）は意図的にどのタスクにも含めていない
- **プレースホルダ**: 全コードブロックは実際に動く完全な内容（`TODO`等なし）
- **型/シグネチャの一貫性**: `get_db_engine`/`get_db_connection`/`DbLifespanResource`の引数・戻り値の型はTask 1〜3で統一。Task 2はTask 1のシグネチャをそのまま再エクスポートするだけで変更していない
- **uncalled_for.Dependsの挙動**: 実装前に実際のインストール済みパッケージ（`.venv/lib/python3.14/site-packages/uncalled_for/functional.py`）を読み、`_resolve_factory_value`が戻り値の型で分岐することを確認した上でTask 3の設計を確定した。さらに一時的な検証用プロジェクトでSQLAlchemyの`:memory:`SQLiteが自動的に`StaticPool`になること（複数connectionで状態が共有される）と、`engine.begin()`が`AbstractAsyncContextManager`のインスタンスであることを実際に実行して確認済み
