# Redis接続lifespan部品 再設計 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** core-toolkit / fastapi-toolkit / fastmcp-toolkitに、`db_lifespan`と対称な名前付き複数インスタンス方式のRedis接続lifespan管理部品（`RedisLifespanResource`/`get_redis_client`とFastMCP向けの`redis_lifespan`/`CurrentRedisClient`）を新規実装し、両パッケージのcacheサンプルを作り直す。

**Architecture:** `db_lifespan`と同じ3層構造（core-toolkitに実体、fastapi-toolkitは薄い再エクスポート、fastmcp-toolkitは`lifespan_context`ベースの独立実装）。旧`redis-wip-stash`ブランチの単一グローバルクライアント方式は採用せず、`name`引数必須の名前付き複数登録APIにする。DBの`get_db_connection`（呼び出し単位のトランザクション境界）に相当するAPIはRedisには設けない。

**Tech Stack:** `redis.asyncio`（redis-py） + `fakeredis`（テストは全てこれで完結、実Redisサーバー不要）。Starlette/FastAPI/FastMCP/`uncalled_for`は既存依存をそのまま利用。

**Spec:** `docs/superpowers/specs/2026-09-23-redis-lifespan-named-multi-instance-design.md`

## Global Constraints

- Python 3.14+（既存パッケージのrequires-pythonに合わせる）
- ruffでE, F, I, UPルールをlint。core-toolkit/fastmcp-toolkitは既定line-length（88）、fastapi-toolkitと`examples/bff`は`line-length = 120`
- 公開API（`RedisLifespanResource`, `get_redis_client`, `redis_lifespan`, `CurrentRedisClient`）には日本語・GoogleスタイルのDocstringを付ける
- 名前付き複数インスタンスAPIのみを提供する。`name`引数を省略できる「単一グローバルクライアント」方式は作らない
- DBの`get_db_connection`（呼び出し単位でbegin/commit/rollback/close）に相当するAPIは設けない。`get_redis_client`／`CurrentRedisClient`のみで完結させる
- テストは`fakeredis.aioredis.FakeRedis`のみを使う。実Redisサーバーへの接続は行わない
- fastmcp-toolkitのRPC越し「未登録」テストは、DBで確定した知見（`a9b2fb9`）通り**Dependsパラメータ名**でmatchし、その理由をコメントで明記する
- pyproject.tomlへの依存追加後は必ず対象ディレクトリで`uv sync --all-extras`を実行してから実装・テストに進む
- fastmcp-toolkitの`examples/`はパッケージ化せず、既存の`simple_server.py`と同じフラットスクリプト構成を踏襲する

---

## Task 1: core-toolkit — `RedisLifespanResource`/`get_redis_client`

**Files:**
- Modify: `core-toolkit/pyproject.toml`
- Create: `core-toolkit/src/core_toolkit/redis_lifespan.py`
- Test: `core-toolkit/tests/test_redis_lifespan.py`

**Interfaces:**
- Consumes: `core_toolkit.lifespan.LifespanResource`（既存、`context(self, app: Starlette) -> AbstractAsyncContextManager`を実装するABC）、`core_toolkit.lifespan.create_lifespan(*resources: LifespanResource) -> Callable[..., AsyncGenerator[None, Any]]`（既存）
- Produces:
  - `RedisLifespanResource(name: str, url: str, **client_kwargs: Any)` — `LifespanResource`のサブクラス
  - `get_redis_client(name: str) -> Callable[[Request], Redis]`
  - これらはTask 2（fastapi-toolkit）がそのまま再エクスポートする

- [ ] **Step 1: pyproject.tomlに`redis` extraと`fakeredis`を追加**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、`dev`の直前へ以下を追加する。

```toml
redis = [
    "redis>=5.0",
]
```

`dev`の配列には`"fakeredis>=2.20",`を追加する（アルファベット順で`"aiohttp>=3.14.1",`の直後）。

- [ ] **Step 2: 依存をインストール**

Run: `cd core-toolkit && uv sync --all-extras`
Expected: `redis`/`fakeredis`が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`core-toolkit/tests/test_redis_lifespan.py`を作成する。

```python
"""redis_lifespanの統合テスト。"""

import pytest
from fakeredis.aioredis import FakeRedis
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_redis_client_returns_registered_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    app = Starlette()
    lifespan = create_lifespan(RedisLifespanResource("cache", "redis://localhost:6379/0"))

    async with lifespan(app):
        client = get_redis_client("cache")(_make_request(app))
        await client.set("k", "v")
        assert await client.get("k") == b"v"


def test_get_redis_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="cache"):
        get_redis_client("cache")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    app = Starlette()
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        RedisLifespanResource("session", "redis://localhost:6379/1"),
    )

    async with lifespan(app):
        cache_client = get_redis_client("cache")(_make_request(app))
        session_client = get_redis_client("session")(_make_request(app))
        assert cache_client is not session_client


@pytest.mark.asyncio
async def test_redis_lifespan_resource_closes_client_on_exit(monkeypatch: pytest.MonkeyPatch):
    closed = False

    class TrackingFakeRedis(FakeRedis):
        async def aclose(self, *args, **kwargs):
            nonlocal closed
            closed = True
            await super().aclose(*args, **kwargs)

    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", TrackingFakeRedis)
    app = Starlette()
    lifespan = create_lifespan(RedisLifespanResource("cache", "redis://localhost:6379/0"))

    async with lifespan(app):
        pass

    assert closed
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd core-toolkit && uv run pytest tests/test_redis_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core_toolkit.redis_lifespan'`）

- [ ] **Step 5: 実装を書く**

`core-toolkit/src/core_toolkit/redis_lifespan.py`を作成する。

```python
"""Starlette/ASGIアプリ向けRedis接続lifespan管理。

名前付きで複数のRedisクライアントを同時に登録・取得できる。

利用には ``core-toolkit[redis]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class RedisLifespanResource(LifespanResource):
    """名前付きでRedisクライアントを登録するリソース。同じappに複数登録できる。

    Args:
        name: このクライアントを識別する名前（例: ``"cache"``）。
            ``get_redis_client`` で同じ名前を指定して取得する。
        url: 接続先のURL（例: ``redis://localhost:6379/0``）。
        client_kwargs: ``Redis.from_url`` にそのまま渡す追加引数。
    """

    def __init__(self, name: str, url: str, **client_kwargs: Any) -> None:
        self._name = name
        self._url = url
        self._client_kwargs = client_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        client = Redis.from_url(self._url, **self._client_kwargs)
        clients: dict[str, Redis] = getattr(app.state, "redis_clients", {})
        app.state.redis_clients = {**clients, self._name: client}

        try:
            yield client
        finally:
            await client.aclose()


def get_redis_client(name: str) -> Callable[[Request], Redis]:
    """名前を指定してRedisクライアントを取得するprovider関数を生成する。

    Args:
        name: ``RedisLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``RedisLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> Redis:
        clients: dict[str, Redis] = getattr(request.app.state, "redis_clients", {})
        if name not in clients:
            raise RuntimeError(
                f"redis_clients['{name}'] is not set. "
                f"Did you forget to register RedisLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_redis_client_{name}"
    return get_client
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd core-toolkit && uv run pytest tests/test_redis_lifespan.py -v`
Expected: PASS（4件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd core-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/uv.lock core-toolkit/src/core_toolkit/redis_lifespan.py core-toolkit/tests/test_redis_lifespan.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add named multi-instance Redis lifespan

RedisLifespanResource/get_redis_clientを追加。db_lifespanと同型の名前付き
複数インスタンスAPIとし、旧WIPの単一グローバルクライアント方式は採用しない。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: fastapi-toolkit — `redis_lifespan.py` 薄いラッパー

**Files:**
- Modify: `fastapi-toolkit/pyproject.toml`
- Create: `fastapi-toolkit/src/fastapi_toolkit/redis_lifespan.py`
- Test: `fastapi-toolkit/tests/test_redis_lifespan.py`

**Interfaces:**
- Consumes: Task 1の`core_toolkit.redis_lifespan.RedisLifespanResource`/`get_redis_client`（シグネチャはTask 1のProducesを参照）、`fastapi_toolkit.lifespan.create_lifespan`（既存）
- Produces: `fastapi_toolkit.redis_lifespan.RedisLifespanResource`/`get_redis_client`（Task 1のものをそのまま再エクスポート、シグネチャ変更なし）。Task 4がこれを使う

- [ ] **Step 1: pyproject.tomlに`redis` extraと`fakeredis`を追加**

`fastapi-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`sqlite`/`postgres`/`mysql`の直後へ以下を追加する（`sqlite`が`"core-toolkit[sqlite]"`を参照している既存パターンに合わせる）。

```toml
redis = [
  "core-toolkit[redis]",
]
```

`dev`の配列には`"fakeredis>=2.20",`を追加する（`"core-toolkit[telemetry]",`の直後）。

- [ ] **Step 2: 依存をインストール**

Run: `cd fastapi-toolkit && uv sync --all-extras`
Expected: core-toolkitの`redis`エクストラ経由で`redis`/`fakeredis`が解決される。

- [ ] **Step 3: 失敗するテストを書く**

`fastapi-toolkit/tests/test_redis_lifespan.py`を作成する。

```python
"""RedisLifespanResource/get_redis_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

RedisLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_redis_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client

CacheRedisClient = Annotated[Redis, Depends(get_redis_client("cache"))]


@pytest.mark.asyncio
async def test_redis_client_resolves_via_depends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = FastAPI()

    @app.put("/cache/{key}")
    async def write(key: str, value: str, redis: CacheRedisClient) -> dict[str, bool]:
        await redis.set(key, value)
        return {"ok": True}

    @app.get("/cache/{key}")
    async def read(key: str, redis: CacheRedisClient) -> dict[str, str | None]:
        result = await redis.get(key)
        return {"value": result.decode() if result is not None else None}

    lifespan = create_lifespan(RedisLifespanResource("cache", "redis://localhost:6379/0"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.put("/cache/greeting", params={"value": "hello"})
            resp = await client.get("/cache/greeting")

    assert resp.status_code == 200
    assert resp.json() == {"value": "hello"}


@pytest.mark.asyncio
async def test_redis_client_returns_none_for_missing_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = FastAPI()

    @app.get("/cache/{key}")
    async def read(key: str, redis: CacheRedisClient) -> dict[str, str | None]:
        result = await redis.get(key)
        return {"value": result.decode() if result is not None else None}

    lifespan = create_lifespan(RedisLifespanResource("cache", "redis://localhost:6379/0"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cache/missing")

    assert resp.status_code == 200
    assert resp.json() == {"value": None}
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_redis_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastapi_toolkit.redis_lifespan'`）

- [ ] **Step 5: 実装を書く**

`fastapi-toolkit/src/fastapi_toolkit/redis_lifespan.py`を作成する。

```python
"""Redis接続lifespanリソースのFastAPI向け薄いラッパー。

``RedisLifespanResource``/``get_redis_client`` はFastAPIに依存せず
``core_toolkit.redis_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[Redis, Depends(get_redis_client("cache"))]``）はアプリ側が
名前を指定して定義する。

利用には ``fastapi-toolkit[redis]`` extraのインストールが必要。
"""

from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client

__all__ = ["RedisLifespanResource", "get_redis_client"]
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_redis_lifespan.py -v`
Expected: PASS（2件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd fastapi-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add fastapi-toolkit/pyproject.toml fastapi-toolkit/uv.lock fastapi-toolkit/src/fastapi_toolkit/redis_lifespan.py fastapi-toolkit/tests/test_redis_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastapi-toolkit): re-export named multi-instance Redis lifespan

core_toolkit.redis_lifespanのRedisLifespanResource/get_redis_clientを
そのまま再エクスポートする薄いラッパーを追加。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: fastmcp-toolkit — `redis_lifespan.py` 独立実装

**Files:**
- Modify: `fastmcp-toolkit/pyproject.toml`
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/redis_lifespan.py`
- Test: `fastmcp-toolkit/tests/test_redis_lifespan.py`

**Interfaces:**
- Consumes: `fastmcp.server.dependencies.CurrentContext`（既存）、`fastmcp.server.lifespan.lifespan`/`Lifespan`（既存、`fastmcp_toolkit/db_lifespan.py`と同じ使い方）、`uncalled_for.Depends`（既存）
- Produces:
  - `redis_lifespan(name: str, url: str, **client_kwargs: Any) -> Lifespan`
  - `_get_redis_client(name: str) -> Callable[[Context], Redis]`（モジュール内部用、テストからも直接importする）
  - `CurrentRedisClient(name: str) -> Redis`
  - これらはTask 5（cacheサンプル）が使う

- [ ] **Step 1: pyproject.tomlに`redis` extraと`fakeredis`を追加**

`fastmcp-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、`dev`の直前へ以下を追加する。

```toml
redis = [
    "redis>=5.0",
]
```

`dev`の配列には`"fakeredis>=2.20",`を追加する（`"core-toolkit[telemetry]",`の直後）。

- [ ] **Step 2: 依存をインストール**

Run: `cd fastmcp-toolkit && uv sync --all-extras`
Expected: `redis`/`fakeredis`が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_redis_lifespan.py`を作成する。

```python
"""redis_lifespan / CurrentRedisClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentRedisClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from fastmcp import Client, FastMCP
from redis.asyncio import Redis

from fastmcp_toolkit.redis_lifespan import CurrentRedisClient, redis_lifespan


@pytest.mark.asyncio
async def test_tool_resolves_redis_client_via_depends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)

    app = FastMCP("test", lifespan=redis_lifespan("cache", "redis://localhost:6379/0"))

    @app.tool
    async def set_and_get(key: str, value: str, redis: Redis = CurrentRedisClient("cache")) -> str:
        await redis.set(key, value)
        result = await redis.get(key)
        return result.decode() if result else ""

    async with Client(app) as client:
        result = await client.call_tool("set_and_get", {"key": "k", "value": "v"})

    assert result.data == "v"


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)

    app = FastMCP(
        "test",
        lifespan=redis_lifespan("cache", "redis://localhost:6379/0")
        | redis_lifespan("session", "redis://localhost:6379/1"),
    )

    @app.tool
    async def compare(
        cache: Redis = CurrentRedisClient("cache"),
        session: Redis = CurrentRedisClient("session"),
    ) -> bool:
        return cache is not session

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(redis: Redis = CurrentRedisClient("cache")) -> str:
        return type(redis).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージ（"redis_clients['cache']
        # is not set..."）は失われ、クライアントに届くのはDepends解決対象の
        # パラメータ名を含む "Failed to resolve dependency '<param>' for <fn>" のみ
        # になる（fastmcp-toolkitのdb_lifespanで確認済み、a9b2fb9参照）。
        # そのため登録名 "cache" ではなくパラメータ名 "redis" でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'redis'"):
            await client.call_tool("whoami", {})


def test_get_redis_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.redis_lifespan import _get_redis_client

    get_client = _get_redis_client("cache")
    with pytest.raises(RuntimeError, match="cache"):
        get_client(SimpleNamespace(lifespan_context={}))
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_redis_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastmcp_toolkit.redis_lifespan'`）

- [ ] **Step 5: 実装を書く**

`fastmcp-toolkit/src/fastmcp_toolkit/redis_lifespan.py`を作成する。

```python
"""FastMCPサーバー向けRedis接続lifespan統合。

Starlette向けの ``core_toolkit.redis_lifespan.RedisLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[redis]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from redis.asyncio import Redis
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"redis_client:{name}"


def redis_lifespan(name: str, url: str, **client_kwargs: Any) -> Lifespan:
    """Redisクライアントのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``redis.asyncio`` クライアントを生成してlifespan_contextに
    格納し、終了時にcloseする。``FastMCP(lifespan=redis_lifespan(name, url))``
    として使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のRedisクライアントを
    同時に登録できる。

    Args:
        name: このクライアントを識別する名前。``CurrentRedisClient`` で
            同じ名前を指定して取得する。
        url: 接続先のURL（例: ``redis://localhost:6379/0``）。
        client_kwargs: ``Redis.from_url`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _redis_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        client = Redis.from_url(url, **client_kwargs)
        try:
            yield {_lifespan_key(name): client}
        finally:
            await client.aclose()

    return _redis_lifespan


def _get_redis_client(name: str) -> Callable[[Context], Redis]:
    def get_client(ctx: Context = CurrentContext()) -> Redis:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=redis_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(Redis, client)

    get_client.__name__ = f"get_redis_client_{name}"
    return get_client


def CurrentRedisClient(name: str) -> Redis:  # noqa: N802
    """``redis_lifespan(name, ...)`` が生成したRedisクライアントを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(redis: Redis = CurrentRedisClient("cache")) -> str:
            ...

    Args:
        name: ``redis_lifespan(name=...)`` に登録した名前。

    Returns:
        Redis: ``Depends(...)`` でラップされた、実行時に解決されるRedis
        クライアント。
    """
    return cast(Redis, Depends(_get_redis_client(name)))
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_redis_lifespan.py -v`
Expected: PASS（4件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd fastmcp-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add fastmcp-toolkit/pyproject.toml fastmcp-toolkit/uv.lock fastmcp-toolkit/src/fastmcp_toolkit/redis_lifespan.py fastmcp-toolkit/tests/test_redis_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastmcp-toolkit): add named multi-instance Redis lifespan

lifespan_contextベースの独立実装でredis_lifespan/CurrentRedisClientを
追加。RPC境界越しの「未登録」テストはDependsパラメータ名でmatchする
（db_lifespanのa9b2fb9と同じ理由）。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: fastapi-toolkit — bffサンプルにcache機能を追加

**Files:**
- Modify: `fastapi-toolkit/examples/bff/pyproject.toml`
- Modify: `fastapi-toolkit/examples/bff/.env.example`
- Modify: `fastapi-toolkit/examples/bff/src/bff/config.py`
- Modify: `fastapi-toolkit/examples/bff/src/bff/app.py`
- Create: `fastapi-toolkit/examples/bff/src/bff/cache/__init__.py`
- Create: `fastapi-toolkit/examples/bff/src/bff/cache/domain.py`
- Create: `fastapi-toolkit/examples/bff/src/bff/cache/repository.py`
- Create: `fastapi-toolkit/examples/bff/src/bff/cache/usecase.py`
- Create: `fastapi-toolkit/examples/bff/src/bff/cache/dependencies.py`
- Create: `fastapi-toolkit/examples/bff/src/bff/cache/router.py`
- Test: `fastapi-toolkit/examples/bff/tests/cache/__init__.py`
- Test: `fastapi-toolkit/examples/bff/tests/cache/test_usecase.py`
- Test: `fastapi-toolkit/examples/bff/tests/cache/test_router.py`

**Interfaces:**
- Consumes: Task 2の`fastapi_toolkit.redis_lifespan.RedisLifespanResource`/`get_redis_client`
- Produces: `bff.cache.domain.CacheRepository`（ABC）、`bff.cache.dependencies._get_cache_redis_client`（テストが`dependency_overrides`のキーとして使う）、`bff.cache.dependencies.get_cache_repository`（同上）。他タスクからは参照されない（末端のサンプル）

- [ ] **Step 1: pyproject.tomlと.env.exampleを更新**

`fastapi-toolkit/examples/bff/pyproject.toml`の`dependencies`にある`"fastapi-toolkit",`を`"fastapi-toolkit[redis]",`に変更する。`[project.optional-dependencies]`の`dev`に`"fakeredis>=2.20",`を追加する（`"httpx>=0.28",`の直後）。

`fastapi-toolkit/examples/bff/.env.example`の末尾に以下を追加する。

```
REDIS_URL=redis://localhost:6379/0
```

- [ ] **Step 2: 依存をインストール**

Run: `cd fastapi-toolkit/examples/bff && uv sync --all-extras`
Expected: `redis`/`fakeredis`が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`fastapi-toolkit/examples/bff/tests/cache/__init__.py`を空ファイルとして作成する。

`fastapi-toolkit/examples/bff/tests/cache/test_usecase.py`を作成する。

```python
"""Usecase層の単体テスト。

Repositoryはインメモリのフェイクに差し替え、Redis・FastAPIのどちらにも
依存せずにUsecaseのロジックだけを検証する。
"""

from bff.cache.domain import CacheRepository
from bff.cache.usecase import GetCachedValueUseCase, SetCachedValueUseCase


class FakeCacheRepository(CacheRepository):
    """インメモリの ``CacheRepository`` フェイク実装。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value


async def test_get_cached_value_usecase_returns_none_when_missing():
    repository: CacheRepository = FakeCacheRepository()
    usecase = GetCachedValueUseCase(repository)

    result = await usecase.execute("missing-key")

    assert result is None


async def test_set_then_get_returns_stored_value():
    repository: CacheRepository = FakeCacheRepository()
    write_usecase = SetCachedValueUseCase(repository)
    read_usecase = GetCachedValueUseCase(repository)

    await write_usecase.execute("greeting", "hello")
    result = await read_usecase.execute("greeting")

    assert result == "hello"
```

`fastapi-toolkit/examples/bff/tests/cache/test_router.py`を作成する。

```python
"""Controller層の結合テスト。

差し替える層の粒度を2パターン示す:

- ``_get_cache_redis_client`` を差し替える: Repository/Usecaseは本物を
  使い、Redisの代わりにfakeredisで動かす（配線全体の疎通確認）。
- ``get_cache_repository`` を差し替える: Redisにすら触れず、
  Controller〜Usecaseの結線だけを検証する。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _override_redis_client():
    from bff.app import app
    from bff.cache.dependencies import _get_cache_redis_client

    # 同一インスタンスを使い回さないと、write用リクエストとread用リクエストで
    # 別々のFakeRedisが解決されてしまい、書き込みが読み込み側に反映されない。
    fake_redis = FakeRedis()
    app.dependency_overrides[_get_cache_redis_client] = lambda: fake_redis
    yield
    app.dependency_overrides.pop(_get_cache_redis_client, None)


async def test_read_cache_returns_404_when_key_missing():
    from bff.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/cache/missing-key")

    assert resp.status_code == 404


async def test_write_then_read_cache_roundtrip():
    from bff.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        write_resp = await client.put("/cache/greeting", json={"value": "hello"})
        read_resp = await client.get("/cache/greeting")

    assert write_resp.status_code == 200
    assert write_resp.json() == {"key": "greeting", "value": "hello"}
    assert read_resp.status_code == 200
    assert read_resp.json() == {"key": "greeting", "value": "hello"}


async def test_read_cache_with_repository_override_bypasses_redis():
    from bff.app import app
    from bff.cache.dependencies import get_cache_repository
    from bff.cache.domain import CacheRepository

    class FixedCacheRepository(CacheRepository):
        async def get(self, key: str) -> str | None:
            return "fixed-value" if key == "known" else None

        async def set(self, key: str, value: str) -> None:
            raise AssertionError("write should not be called in this test")

    app.dependency_overrides[get_cache_repository] = lambda: FixedCacheRepository()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cache/known")
    finally:
        app.dependency_overrides.pop(get_cache_repository, None)

    assert resp.status_code == 200
    assert resp.json() == {"key": "known", "value": "fixed-value"}
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastapi-toolkit/examples/bff && uv run pytest tests/cache/ -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'bff.cache'`）

- [ ] **Step 5: cache機能を実装**

`fastapi-toolkit/examples/bff/src/bff/cache/__init__.py`を空ファイルとして作成する。

`fastapi-toolkit/examples/bff/src/bff/cache/domain.py`を作成する。

```python
"""キャッシュ機能のドメイン層。

Usecase層はこのモジュールが定義する抽象にのみ依存し、
Redis等の具体的な実装（Repository層）を一切知らない。
"""

from abc import ABC, abstractmethod


class CacheRepository(ABC):
    """キー・バリュー形式のキャッシュへのアクセスを抽象化するインターフェース。"""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """キーに対応する値を取得する。存在しなければ ``None`` を返す。"""
        ...

    @abstractmethod
    async def set(self, key: str, value: str) -> None:
        """キーに値を設定する。"""
        ...
```

`fastapi-toolkit/examples/bff/src/bff/cache/repository.py`を作成する。

```python
"""キャッシュ機能のインフラ層。Redisを使った ``CacheRepository`` の実装。"""

from redis.asyncio import Redis

from bff.cache.domain import CacheRepository


class RedisCacheRepository(CacheRepository):
    """Redisクライアントによる ``CacheRepository`` の実装。"""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        """キーに対応する値をRedisから取得する。存在しなければ ``None`` を返す。"""
        value = await self._redis.get(key)
        return value.decode() if value is not None else None

    async def set(self, key: str, value: str) -> None:
        """キーに値をRedisへ設定する。"""
        await self._redis.set(key, value)
```

`fastapi-toolkit/examples/bff/src/bff/cache/usecase.py`を作成する。

```python
"""キャッシュ機能のUsecase層。``CacheRepository`` インターフェースのみに依存する。

FastAPIやRedisといった外側の層のモジュールは一切importしない。
"""

from dataclasses import dataclass

from bff.cache.domain import CacheRepository


@dataclass
class GetCachedValueUseCase:
    """キャッシュから値を取得するユースケース。"""

    repository: CacheRepository

    async def execute(self, key: str) -> str | None:
        """キーに対応する値を返す。存在しなければ ``None`` を返す。"""
        return await self.repository.get(key)


@dataclass
class SetCachedValueUseCase:
    """キャッシュに値を設定するユースケース。"""

    repository: CacheRepository

    async def execute(self, key: str, value: str) -> None:
        """キーに値を設定する。"""
        await self.repository.set(key, value)
```

`fastapi-toolkit/examples/bff/src/bff/cache/dependencies.py`を作成する。

```python
"""キャッシュ機能のDI配線。

Controller層(FastAPI)からのみ ``Depends`` を扱い、Usecase/Repositoryへは
解決済みのオブジェクトを明示的な引数として渡す。この関数群がRedisという
具体的な実装とUsecaseが依存する抽象(``CacheRepository``)を結びつける
Composition Rootにあたる。
"""

from typing import Annotated

from fastapi import Depends
from fastapi_toolkit.redis_lifespan import get_redis_client
from redis.asyncio import Redis

from bff.cache.domain import CacheRepository
from bff.cache.repository import RedisCacheRepository
from bff.cache.usecase import GetCachedValueUseCase, SetCachedValueUseCase

# 名前付きAPIのため get_redis_client("cache") が返すクロージャを一度だけ
# 生成してモジュール属性に固定する。テストの dependency_overrides はこの
# オブジェクト（呼び出すたびに新しい関数が生成される get_redis_client 自体
# ではない）をキーにする必要がある。
_get_cache_redis_client = get_redis_client("cache")
CacheRedisClient = Annotated[Redis, Depends(_get_cache_redis_client)]


def get_cache_repository(redis: CacheRedisClient) -> CacheRepository:
    """``CacheRepository`` の実装を組み立てる。

    Args:
        redis: 名前 ``"cache"`` で登録されたRedisクライアント。

    Returns:
        CacheRepository: Redisを使った具体的な実装。
    """
    return RedisCacheRepository(redis)


CacheRepositoryDep = Annotated[CacheRepository, Depends(get_cache_repository)]


def get_read_cache_usecase(repository: CacheRepositoryDep) -> GetCachedValueUseCase:
    """``GetCachedValueUseCase`` を組み立てる。"""
    return GetCachedValueUseCase(repository)


def get_write_cache_usecase(repository: CacheRepositoryDep) -> SetCachedValueUseCase:
    """``SetCachedValueUseCase`` を組み立てる。"""
    return SetCachedValueUseCase(repository)


ReadCacheUseCaseDep = Annotated[GetCachedValueUseCase, Depends(get_read_cache_usecase)]
WriteCacheUseCaseDep = Annotated[SetCachedValueUseCase, Depends(get_write_cache_usecase)]
```

`fastapi-toolkit/examples/bff/src/bff/cache/router.py`を作成する。

```python
"""キャッシュ機能のController層(FastAPIエンドポイント)。

``Depends`` を扱うのはこのファイルと ``dependencies.py`` のみ。
Usecase/Repositoryは ``fastapi`` を一切importしない。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bff.cache.dependencies import ReadCacheUseCaseDep, WriteCacheUseCaseDep

router = APIRouter(prefix="/cache", tags=["cache"])


class SetCacheValueRequest(BaseModel):
    """キャッシュ値の設定リクエストボディ。"""

    value: str


class CacheValueResponse(BaseModel):
    """キャッシュ値のレスポンスボディ。"""

    key: str
    value: str


@router.get("/{key}")
async def read_cache(key: str, usecase: ReadCacheUseCaseDep) -> CacheValueResponse:
    """キーに対応する値をキャッシュから取得する。存在しなければ404を返す。"""
    value = await usecase.execute(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return CacheValueResponse(key=key, value=value)


@router.put("/{key}")
async def write_cache(key: str, body: SetCacheValueRequest, usecase: WriteCacheUseCaseDep) -> CacheValueResponse:
    """キーに値を設定する。"""
    await usecase.execute(key, body.value)
    return CacheValueResponse(key=key, value=body.value)
```

- [ ] **Step 6: config.pyとapp.pyを修正**

`fastapi-toolkit/examples/bff/src/bff/config.py`の`backend_b_url`フィールドの直後（52行目付近）に以下を追加する。

```python
    redis_url: str = field(default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
```

`fastapi-toolkit/examples/bff/src/bff/app.py`を以下のように変更する。

```python
"""FastAPIアプリケーション定義。"""

import aiohttp
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_toolkit import setup_logging
from fastapi_toolkit.lifespan import AioHttpLifespanResource, create_lifespan
from fastapi_toolkit.logging import get_logger
from fastapi_toolkit.redis_lifespan import RedisLifespanResource
from starlette.middleware.sessions import SessionMiddleware

from bff.auth import router as auth_router
from bff.cache.router import router as cache_router
from bff.config import load_application_settings
from bff.token_cache import get_token_cache, save_token_cache

setup_logging(application_id="BFF")

logger = get_logger(__name__)
settings = load_application_settings()

app = FastAPI(
    title="BFF",
    lifespan=create_lifespan(
        AioHttpLifespanResource(),
        RedisLifespanResource("cache", settings.redis_url),
    ),
)

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.include_router(auth_router)
app.include_router(cache_router)

app.state.azure_settings = settings


@app.get("/hello")
async def hello() -> dict[str, str]:
    """挨拶メッセージを返す。"""
    return {"message": "hello"}


@app.get("/backend/a")
async def backend_a(req: Request) -> dict[str, str]:
    """バックエンドAPI Aにアクセスする"""
    session: aiohttp.ClientSession = req.app.state.http_client
    async with session.get(settings.backend_a_url) as response:
        return await response.json()


@app.get("/backend/b")
async def backend_b(req: Request) -> JSONResponse:
    """バックエンドAPI Bにアクセスする"""
    import msal

    user = req.session.get("user")
    if user is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    oid = user.get("oid", "")
    cache = get_token_cache(oid)
    msal_app = msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=settings.client_secret,
        authority=settings.authority,
        token_cache=cache,
    )

    accounts = msal_app.get_accounts()
    if not accounts:
        return JSONResponse({"error": "no cached account, re-login required"}, status_code=401)

    result = msal_app.acquire_token_silent(scopes=settings.backend_b_scopes, account=accounts[0])
    if result is None or "error" in result:
        return JSONResponse({"error": "token acquisition failed, re-login required"}, status_code=401)

    save_token_cache(oid, cache)
    access_token = result["access_token"]

    session: aiohttp.ClientSession = req.app.state.http_client
    async with session.get(
        settings.backend_b_url,
        headers={"Authorization": f"Bearer {access_token}"},
    ) as response:
        data = await response.json()
    return JSONResponse(data)


@app.get("/backend/b/c")
async def backend_b_c(req: Request) -> dict[str, str]:
    """バックエンドAPI BのCエンドポイントにアクセスする"""
    session: aiohttp.ClientSession = req.app.state.http_client
    async with session.get(settings.backend_b_url + "/c") as response:
        return await response.json()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_config=None)
```

（変更点は import 3行の追加、`RedisLifespanResource("cache", settings.redis_url)` の登録、`cache_router` の include のみ。`/hello`以降のエンドポイントは変更なし）

- [ ] **Step 7: テストを実行し成功を確認**

Run: `cd fastapi-toolkit/examples/bff && uv run pytest tests/cache/ -v`
Expected: PASS（5件全て）

- [ ] **Step 8: 既存テストが壊れていないことを確認**

Run: `cd fastapi-toolkit/examples/bff && uv run pytest -v`
Expected: 全件PASS（`test_auth.py`/`test_hello.py`含む）

- [ ] **Step 9: lint/formatを実行**

Run: `cd fastapi-toolkit/examples/bff && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 10: コミット**

```bash
git add fastapi-toolkit/examples/bff/pyproject.toml fastapi-toolkit/examples/bff/uv.lock fastapi-toolkit/examples/bff/.env.example fastapi-toolkit/examples/bff/src/bff/config.py fastapi-toolkit/examples/bff/src/bff/app.py fastapi-toolkit/examples/bff/src/bff/cache/ fastapi-toolkit/examples/bff/tests/cache/
git commit -m "$(cat <<'EOF'
feat(fastapi-toolkit): add cache example using named Redis lifespan

Depends連鎖のprovider方式(B案)でCacheRepository/Usecase/Controllerを
配線するリファレンス実装をbffサンプルに追加。dependency_overridesの
キーには get_redis_client("cache") 自体ではなく、モジュールに固定した
クロージャ _get_cache_redis_client を使う。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: fastmcp-toolkit — cacheサンプルを再作成

**Files:**
- Create: `fastmcp-toolkit/examples/cache_domain.py`
- Create: `fastmcp-toolkit/examples/cache_repository.py`
- Create: `fastmcp-toolkit/examples/cache_usecase.py`
- Create: `fastmcp-toolkit/examples/cache_dependencies.py`
- Create: `fastmcp-toolkit/examples/cache_server.py`
- Test: `fastmcp-toolkit/examples/test_cache.py`

**Interfaces:**
- Consumes: Task 3の`fastmcp_toolkit.redis_lifespan.redis_lifespan`/`CurrentRedisClient`
- Produces: なし（末端のサンプル）

- [ ] **Step 1: 失敗するテストを書く**

`fastmcp-toolkit/examples/test_cache.py`を作成する。`examples/`はパッケージ化されておらず`cache_*.py`はディレクトリ相対の裸importを前提にしているため、このテストも同じディレクトリに置き、実行時にそのディレクトリがカレントディレクトリの一部としてsys.pathに追加される前提で書く。

```python
"""cache_server.py（fastmcp-toolkitのredis_lifespanサンプル）の動作確認テスト。

examples/配下はパッケージ化されていないフラットスクリプト構成のため、この
テストもexamples/に同居させ、``cd fastmcp-toolkit && uv run pytest
examples/test_cache.py -v`` のように実行する。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from fastmcp import Client

from cache_server import app


@pytest.mark.asyncio
async def test_write_then_read_cache_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)

    async with Client(app) as client:
        await client.call_tool("write_cache", {"key": "greeting", "value": "hello"})
        result = await client.call_tool("read_cache", {"key": "greeting"})

    assert result.data == "hello"


@pytest.mark.asyncio
async def test_read_cache_returns_empty_string_when_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)

    async with Client(app) as client:
        result = await client.call_tool("read_cache", {"key": "missing"})

    assert result.data == ""
```

- [ ] **Step 2: テストを実行し失敗を確認**

Run: `cd fastmcp-toolkit && uv run pytest examples/test_cache.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'cache_server'`）

- [ ] **Step 3: cacheサンプルを実装**

`fastmcp-toolkit/examples/cache_domain.py`を作成する。

```python
"""キャッシュ機能のドメイン層。

Usecase層はこのモジュールが定義する抽象にのみ依存し、
Redis等の具体的な実装（Repository層）を一切知らない。
"""

from abc import ABC, abstractmethod


class CacheRepository(ABC):
    """キー・バリュー形式のキャッシュへのアクセスを抽象化するインターフェース。"""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """キーに対応する値を取得する。存在しなければ ``None`` を返す。"""
        ...

    @abstractmethod
    async def set(self, key: str, value: str) -> None:
        """キーに値を設定する。"""
        ...
```

`fastmcp-toolkit/examples/cache_repository.py`を作成する。

```python
"""キャッシュ機能のインフラ層。Redisを使った ``CacheRepository`` の実装。"""

from cache_domain import CacheRepository
from redis.asyncio import Redis


class RedisCacheRepository(CacheRepository):
    """Redisクライアントによる ``CacheRepository`` の実装。"""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        """キーに対応する値をRedisから取得する。存在しなければ ``None`` を返す。"""
        value = await self._redis.get(key)
        return value.decode() if value is not None else None

    async def set(self, key: str, value: str) -> None:
        """キーに値をRedisへ設定する。"""
        await self._redis.set(key, value)
```

`fastmcp-toolkit/examples/cache_usecase.py`を作成する。

```python
"""キャッシュ機能のUsecase層。

``CacheRepository`` インターフェースのみに依存し、``fastmcp``/``uncalled_for``
といったController層のモジュールは一切importしない。``CacheUsecase`` は
ABCでインターフェース化してあり、取得・設定を分けず1つのUsecaseにまとめて
いる（呼び出し側は差し替え可能な単一の窓口だけを意識すればよい）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from cache_domain import CacheRepository


class CacheUsecase(ABC):
    """キャッシュの取得・設定を行うユースケースのインターフェース。"""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """キーに対応する値を返す。存在しなければ ``None`` を返す。"""
        ...

    @abstractmethod
    async def set(self, key: str, value: str) -> None:
        """キーに値を設定する。"""
        ...


@dataclass
class CacheUsecaseImpl(CacheUsecase):
    """``CacheRepository`` に処理を委譲する ``CacheUsecase`` の実装。"""

    repository: CacheRepository

    async def get(self, key: str) -> str | None:
        return await self.repository.get(key)

    async def set(self, key: str, value: str) -> None:
        await self.repository.set(key, value)
```

`fastmcp-toolkit/examples/cache_dependencies.py`を作成する。

```python
"""キャッシュ機能のDI配線。

``Depends`` を扱うのはこのファイルと ``cache_server.py`` のみ。
Usecase/Repositoryは ``fastmcp``/``uncalled_for`` を一切importしない。
"""

from typing import cast

from cache_domain import CacheRepository
from cache_repository import RedisCacheRepository
from cache_usecase import CacheUsecase, CacheUsecaseImpl
from redis.asyncio import Redis
from uncalled_for import Depends

from fastmcp_toolkit.redis_lifespan import CurrentRedisClient


def get_cache_repository(redis: Redis = CurrentRedisClient("cache")) -> CacheRepository:
    """``CacheRepository`` の実装を組み立てる。"""
    return RedisCacheRepository(redis)


def CurrentCacheRepository() -> CacheRepository:  # noqa: N802
    return cast(CacheRepository, Depends(get_cache_repository))


def get_cache_usecase(
    repository: CacheRepository = CurrentCacheRepository(),
) -> CacheUsecase:
    """``CacheUsecase`` を組み立てる。"""
    return CacheUsecaseImpl(repository)


def CurrentCacheUsecase() -> CacheUsecase:  # noqa: N802
    return cast(CacheUsecase, Depends(get_cache_usecase))
```

`fastmcp-toolkit/examples/cache_server.py`を作成する。

```python
"""fastmcp-toolkitのredis_lifespanを使ったキャッシュサーバーの例。

Controller層(@app.tool)からUsecase/Repositoryへのつなぎ方を示すサンプル。
cache_domain/cache_repository/cache_usecaseはfastmcp/uncalled_forに一切
依存しない素のPythonコードで、fastapi-toolkit向けに書いた場合と同じものを
そのまま使い回せる。
"""

from cache_dependencies import CurrentCacheUsecase
from cache_usecase import CacheUsecase
from fastmcp import FastMCP

from fastmcp_toolkit import run_server
from fastmcp_toolkit.logging import get_logger
from fastmcp_toolkit.redis_lifespan import redis_lifespan

logger = get_logger(__name__)

app = FastMCP("cache-server", lifespan=redis_lifespan("cache", "redis://localhost:6379/0"))


@app.tool
async def read_cache(key: str, usecase: CacheUsecase = CurrentCacheUsecase()) -> str:
    """キャッシュから値を取得する。存在しなければ空文字を返す。"""
    value = await usecase.get(key)
    logger.info("read_cache", key=key, found=value is not None)
    return value if value is not None else ""


@app.tool
async def write_cache(
    key: str, value: str, usecase: CacheUsecase = CurrentCacheUsecase()
) -> str:
    """キャッシュに値を設定する。"""
    await usecase.set(key, value)
    logger.info("write_cache", key=key)
    return "ok"


if __name__ == "__main__":
    run_server(app)
```

- [ ] **Step 4: テストを実行し成功を確認**

Run: `cd fastmcp-toolkit && uv run pytest examples/test_cache.py -v`
Expected: PASS（2件全て）

- [ ] **Step 5: lint/formatを実行（新規追加ファイルのみ対象）**

Run: `cd fastmcp-toolkit && uv run ruff format examples/cache_domain.py examples/cache_repository.py examples/cache_usecase.py examples/cache_dependencies.py examples/cache_server.py examples/test_cache.py && uv run ruff check --fix examples/cache_domain.py examples/cache_repository.py examples/cache_usecase.py examples/cache_dependencies.py examples/cache_server.py examples/test_cache.py`
Expected: エラーなし（`examples/simple_server.py`など既存ファイルは対象に含めない）

- [ ] **Step 6: コミット**

```bash
git add fastmcp-toolkit/examples/cache_domain.py fastmcp-toolkit/examples/cache_repository.py fastmcp-toolkit/examples/cache_usecase.py fastmcp-toolkit/examples/cache_dependencies.py fastmcp-toolkit/examples/cache_server.py fastmcp-toolkit/examples/test_cache.py
git commit -m "$(cat <<'EOF'
feat(fastmcp-toolkit): add cache example using named Redis lifespan

fastapi-toolkitのbffサンプルと同じDepends連鎖のprovider方式で
CacheRepository/Usecase/Controllerを配線するリファレンス実装を
examples/に追加。旧WIPには無かった動作確認テストも新設した。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 自己レビューで確認した点

- **spec網羅性**: specの「意思決定サマリー」全5項目（クライアント管理方式/旧WIPの扱い/トランザクション境界相当API/cacheサンプル構造/RPC例外テスト方法）は全タスクのコード・テストに反映済み。「非スコープ」節の各項目（`get_db_connection`相当API・Repository/Usecaseのtoolkit本体への組み込み・Cluster/Sentinel対応・`redis-wip-stash`ブランチ自体の統合）は意図的にどのタスクにも含めていない
- **プレースホルダ**: 全コードブロックは実際に動く完全な内容（`TODO`等なし）
- **型/シグネチャの一貫性**: `RedisLifespanResource(name, url, **kwargs)`/`get_redis_client(name)`/`redis_lifespan(name, url, **kwargs)`/`CurrentRedisClient(name)`の引数・戻り値の型はTask 1〜3で統一。Task 2はTask 1のシグネチャをそのまま再エクスポートするだけで変更していない。Task 4/5はTask 2/3のAPIをそのまま呼び出すだけで新たなシグネチャを追加していない
- **`dependency_overrides`のキー問題**: 名前付きAPI化により`get_redis_client(name)`は呼び出すたびに新しいクロージャを返すため、旧WIPの「`get_redis_client`自体をoverrideキーにする」パターンはそのままでは動かない。Task 4の`dependencies.py`でモジュール属性`_get_cache_redis_client`として固定し、Task 4のテストではこれをoverrideキーに使うよう変更した
- **fastmcp-toolkitのexamplesテスト実行方法**: `examples/`はpyprojectの`testpaths = ["tests"]`に含まれないため、デフォルトの`uv run pytest`では収集されない。Task 5の各Runコマンドで`examples/test_cache.py`という明示パスを指定している
