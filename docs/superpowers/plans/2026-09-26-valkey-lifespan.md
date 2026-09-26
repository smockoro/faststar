# Valkey Lifespan部品 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`の3パッケージに、`valkey-glide`ベースの名前付き複数インスタンス対応Valkey接続lifespan部品を追加する。

**Architecture:** 既存の`redis_lifespan.py`と同型の3層構造（core-toolkitに実体、fastapi-toolkitは薄い再エクスポート、fastmcp-toolkitは独立実装）を踏襲する。`ValkeyLifespanResource`/`get_valkey_client`/`valkey_lifespan`/`CurrentValkeyClient`はそれぞれ`RedisLifespanResource`/`get_redis_client`/`redis_lifespan`/`CurrentRedisClient`と1対1対応。ただし初期化引数は`url: str`ではなく`host: str, port: int, **config_kwargs`とし、`glide`パッケージの型（`GlideClientConfiguration`/`NodeAddress`）をtoolkit内部に閉じ込める。

**Tech Stack:** Python 3.14 / `valkey-glide`（公式Rustコア非同期クライアント） / Starlette / FastAPI / FastMCP / pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-09-26-valkey-lifespan-design.md`

## Global Constraints

- Python 3.14+、ruffで`E, F, I, UP`ルールをリント・フォーマット
- 公開APIのdocstringは日本語・Googleスタイル
- `valkey`extra名で`valkey-glide`を追加（バージョン下限は`uv add`で実装時に解決）
- 初期化APIは`host: str, port: int = 6379, **config_kwargs: Any`のみ。`GlideClientConfiguration`を丸ごと受け取るAPIにはしない（利用側に`glide`の型を漏らさないため）
- 複数アドレス（レプリカ・クラスタ）は非スコープ。`NodeAddress`は常に1個のみ生成する
- Batch/Transaction相当のAPIラップは行わない
- テストは`fakeredis`相当が存在しないため、`GlideClient.create`をクラスレベルで`monkeypatch`したフェイククライアントを使う（`GlideClient`は`glide`パッケージが提供する単一のクラスオブジェクトなので、どのモジュール経由でimportしても同じ属性を指す。1箇所でのパッチが全モジュールに効く）
- cache利用サンプル（`examples/`配下）の新規作成は非スコープ

---

### Task 1: core-toolkit — `ValkeyLifespanResource` / `get_valkey_client`

**Files:**
- Modify: `core-toolkit/pyproject.toml`
- Create: `core-toolkit/src/core_toolkit/valkey_lifespan.py`
- Test: `core-toolkit/tests/test_valkey_lifespan.py`

**Interfaces:**
- Consumes: `core_toolkit.lifespan.LifespanResource`（既存の抽象基底クラス、`context(self, app: Starlette) -> AbstractAsyncContextManager`を実装する）、`core_toolkit.lifespan.create_lifespan(*resources)`（既存、テストで使用）
- Produces:
  - `ValkeyLifespanResource(name: str, host: str, port: int = 6379, **config_kwargs: Any)` — `LifespanResource`のサブクラス
  - `get_valkey_client(name: str) -> Callable[[Request], GlideClient]`
  - これらはTask 2（fastapi-toolkit）が`from core_toolkit.valkey_lifespan import ValkeyLifespanResource, get_valkey_client`として再エクスポートする

- [ ] **Step 1: `valkey`extraをpyproject.tomlに追加し、依存関係を解決する**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に以下を追記する（`redis = [...]`の直後など、既存の並びに合わせる）:

```toml
valkey = [
    "valkey-glide>=1.3",
]
```

追記後、以下を実行してバージョン下限を実際に解決し直す。

```bash
cd core-toolkit
uv add valkey-glide --optional valkey
```

`pyproject.toml`の`valkey-glide>=...`のバージョン下限が実環境で解決されたものに更新されていることを確認する。

- [ ] **Step 2: `uv sync --all-extras`を実行し、`glide`パッケージがimportできることを確認する**

Run: `cd core-toolkit && uv sync --all-extras && uv run python -c "from glide import GlideClient, GlideClientConfiguration, NodeAddress; print('ok')"`
Expected: `ok`と出力される

- [ ] **Step 3: 失敗するテストを書く**

`core-toolkit/tests/test_valkey_lifespan.py`を新規作成する。

```python
"""valkey_lifespanの統合テスト。"""

import pytest
from glide import GlideClient
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.valkey_lifespan import ValkeyLifespanResource, get_valkey_client


class FakeGlideClient:
    """テスト用のGlideClient代替。set/get/closeのみサポートする。

    GLIDE公式にはfakeredis相当のフェイクサーバーが存在しないため、
    ``GlideClient.create``をクラスレベルでmonkeypatchして返す代替実装として使う。
    """

    def __init__(self) -> None:
        self.closed = False
        self._store: dict[str, str] = {}

    async def close(self) -> None:
        self.closed = True

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_valkey_client_returns_registered_client(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)
    app = Starlette()
    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))

    async with lifespan(app):
        client = get_valkey_client("cache")(_make_request(app))
        await client.set("k", "v")
        assert await client.get("k") == "v"


def test_get_valkey_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="cache"):
        get_valkey_client("cache")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)
    app = Starlette()
    lifespan = create_lifespan(
        ValkeyLifespanResource("cache", "localhost", 6379),
        ValkeyLifespanResource("session", "localhost", 6379),
    )

    async with lifespan(app):
        cache_client = get_valkey_client("cache")(_make_request(app))
        session_client = get_valkey_client("session")(_make_request(app))
        assert cache_client is not session_client

        # objectとしての別物性だけでなく、"cache"への書き込みが"session"側の
        # キーには見えないこと（名前ごとに正しくキー分けされていること）も
        # 検証する。
        await cache_client.set("k", "v")
        assert await session_client.get("k") is None


@pytest.mark.asyncio
async def test_valkey_lifespan_resource_closes_client_on_exit(
    monkeypatch: pytest.MonkeyPatch,
):
    created: list[FakeGlideClient] = []

    async def fake_create(config):
        client = FakeGlideClient()
        created.append(client)
        return client

    monkeypatch.setattr(GlideClient, "create", fake_create)
    app = Starlette()
    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))

    async with lifespan(app):
        pass

    assert created[0].closed
```

- [ ] **Step 4: テストを実行し、失敗することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_valkey_lifespan.py -v`
Expected: `ModuleNotFoundError: No module named 'core_toolkit.valkey_lifespan'`でFAIL

- [ ] **Step 5: 実装を書く**

`core-toolkit/src/core_toolkit/valkey_lifespan.py`を新規作成する。

```python
"""Starlette/ASGIアプリ向けValkey接続lifespan管理。

名前付きで複数のValkeyクライアントを同時に登録・取得できる。

利用には ``core-toolkit[valkey]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from glide import GlideClient, GlideClientConfiguration, NodeAddress
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class ValkeyLifespanResource(LifespanResource):
    """名前付きでValkeyクライアントを登録するリソース。同じappに複数登録できる。

    Args:
        name: このクライアントを識別する名前（例: ``"cache"``）。
            ``get_valkey_client`` で同じ名前を指定して取得する。
        host: 接続先ホスト名。
        port: 接続先ポート番号。
        config_kwargs: ``GlideClientConfiguration`` にそのまま渡す追加引数
            （``use_tls``、``database_id``、``request_timeout``、
            ``credentials``、``reconnect_strategy`` など）。
    """

    def __init__(self, name: str, host: str, port: int = 6379, **config_kwargs: Any) -> None:
        self._name = name
        self._host = host
        self._port = port
        self._config_kwargs = config_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        config = GlideClientConfiguration(
            addresses=[NodeAddress(self._host, self._port)],
            **self._config_kwargs,
        )
        client = await GlideClient.create(config)
        clients: dict[str, GlideClient] = getattr(app.state, "valkey_clients", {})
        app.state.valkey_clients = {**clients, self._name: client}

        try:
            yield client
        finally:
            await client.close()


def get_valkey_client(name: str) -> Callable[[Request], GlideClient]:
    """名前を指定してValkeyクライアントを取得するprovider関数を生成する。

    Args:
        name: ``ValkeyLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``ValkeyLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> GlideClient:
        clients: dict[str, GlideClient] = getattr(request.app.state, "valkey_clients", {})
        if name not in clients:
            raise RuntimeError(
                f"valkey_clients['{name}'] is not set. "
                f"Did you forget to register ValkeyLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_valkey_client_{name}"
    return get_client
```

- [ ] **Step 6: テストを実行し、成功することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_valkey_lifespan.py -v`
Expected: 4件すべてPASS

- [ ] **Step 7: リントとフォーマットを確認する**

Run: `cd core-toolkit && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
cd core-toolkit
git add pyproject.toml src/core_toolkit/valkey_lifespan.py tests/test_valkey_lifespan.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add valkey-glide based lifespan resource

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: fastapi-toolkit — 薄い再エクスポート

**Files:**
- Modify: `fastapi-toolkit/pyproject.toml`
- Create: `fastapi-toolkit/src/fastapi_toolkit/valkey_lifespan.py`
- Test: `fastapi-toolkit/tests/test_valkey_lifespan.py`

**Interfaces:**
- Consumes: Task 1が生成した`core_toolkit.valkey_lifespan.ValkeyLifespanResource`/`core_toolkit.valkey_lifespan.get_valkey_client`（そのまま再エクスポートする）、既存の`fastapi_toolkit.lifespan.create_lifespan`
- Produces: `fastapi_toolkit.valkey_lifespan.ValkeyLifespanResource`/`fastapi_toolkit.valkey_lifespan.get_valkey_client`（Task 1と同一オブジェクトの再エクスポート）

- [ ] **Step 1: `valkey`extraをpyproject.tomlに追加する**

`fastapi-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis = ["core-toolkit[redis]"]`の直後などに以下を追記する。

```toml
valkey = [
  "core-toolkit[valkey]",
]
```

- [ ] **Step 2: 依存解決を確認する**

Run: `cd fastapi-toolkit && uv sync --all-extras && uv run python -c "from glide import GlideClient; print('ok')"`
Expected: `ok`と出力される（core-toolkitの`valkey`extra経由で`valkey-glide`が入っていることの確認）

- [ ] **Step 3: 失敗するテストを書く**

`fastapi-toolkit/tests/test_valkey_lifespan.py`を新規作成する。

```python
"""ValkeyLifespanResource/get_valkey_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

ValkeyLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_valkey_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from glide import GlideClient
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.valkey_lifespan import ValkeyLifespanResource, get_valkey_client

CacheValkeyClient = Annotated[GlideClient, Depends(get_valkey_client("cache"))]


class FakeGlideClient:
    def __init__(self) -> None:
        self.closed = False
        self._store: dict[str, str] = {}

    async def close(self) -> None:
        self.closed = True

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


@pytest.mark.asyncio
async def test_valkey_client_resolves_via_depends(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastAPI()

    @app.put("/cache/{key}")
    async def write(key: str, value: str, valkey: CacheValkeyClient) -> dict[str, bool]:
        await valkey.set(key, value)
        return {"ok": True}

    @app.get("/cache/{key}")
    async def read(key: str, valkey: CacheValkeyClient) -> dict[str, str | None]:
        result = await valkey.get(key)
        return {"value": result}

    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.put("/cache/greeting", params={"value": "hello"})
            resp = await client.get("/cache/greeting")

    assert resp.status_code == 200
    assert resp.json() == {"value": "hello"}


@pytest.mark.asyncio
async def test_valkey_client_returns_none_for_missing_key(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastAPI()

    @app.get("/cache/{key}")
    async def read(key: str, valkey: CacheValkeyClient) -> dict[str, str | None]:
        result = await valkey.get(key)
        return {"value": result}

    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cache/missing")

    assert resp.status_code == 200
    assert resp.json() == {"value": None}
```

- [ ] **Step 4: テストを実行し、失敗することを確認する**

Run: `cd fastapi-toolkit && uv run pytest tests/test_valkey_lifespan.py -v`
Expected: `ModuleNotFoundError: No module named 'fastapi_toolkit.valkey_lifespan'`でFAIL

- [ ] **Step 5: 実装を書く**

`fastapi-toolkit/src/fastapi_toolkit/valkey_lifespan.py`を新規作成する。

```python
"""Valkey接続lifespanリソースのFastAPI向け薄いラッパー。

``ValkeyLifespanResource``/``get_valkey_client`` はFastAPIに依存せず
``core_toolkit.valkey_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[GlideClient, Depends(get_valkey_client("cache"))]``）は
アプリ側が名前を指定して定義する。

利用には ``fastapi-toolkit[valkey]`` extraのインストールが必要。
"""

from core_toolkit.valkey_lifespan import ValkeyLifespanResource, get_valkey_client

__all__ = ["ValkeyLifespanResource", "get_valkey_client"]
```

- [ ] **Step 6: テストを実行し、成功することを確認する**

Run: `cd fastapi-toolkit && uv run pytest tests/test_valkey_lifespan.py -v`
Expected: 2件すべてPASS

- [ ] **Step 7: リントとフォーマットを確認する**

Run: `cd fastapi-toolkit && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
cd fastapi-toolkit
git add pyproject.toml src/fastapi_toolkit/valkey_lifespan.py tests/test_valkey_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastapi-toolkit): re-export valkey lifespan from core-toolkit

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: fastmcp-toolkit — `valkey_lifespan` / `CurrentValkeyClient`

**Files:**
- Modify: `fastmcp-toolkit/pyproject.toml`
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/valkey_lifespan.py`
- Test: `fastmcp-toolkit/tests/test_valkey_lifespan.py`

**Interfaces:**
- Consumes: `fastmcp.server.lifespan.Lifespan`/`fastmcp.server.lifespan.lifespan`（既存）、`fastmcp.server.dependencies.CurrentContext`（既存）、`uncalled_for.Depends`（既存、`fastmcp`が内部で使用）
- Produces:
  - `valkey_lifespan(name: str, host: str, port: int = 6379, **config_kwargs: Any) -> Lifespan`
  - `CurrentValkeyClient(name: str) -> GlideClient`
  - `_get_valkey_client(name: str) -> Callable[[Context], GlideClient]`（テストから直接呼ばれる、非公開）
  - Task 1・Task 2とはコードを共有しない独立実装（`redis_lifespan.py`とfastmcp版`redis_lifespan.py`の関係と同じ）

- [ ] **Step 1: `valkey`extraをpyproject.tomlに追加する**

`fastmcp-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis = ["redis>=5.0"]`の直後などに以下を追記する。

```toml
valkey = [
    "valkey-glide>=1.3",
]
```

（Task 1でcore-toolkit側の下限を`uv add`で解決済みなら、同じ下限値に揃える）

- [ ] **Step 2: 依存解決を確認する**

Run: `cd fastmcp-toolkit && uv sync --all-extras && uv run python -c "from glide import GlideClient, GlideClientConfiguration, NodeAddress; print('ok')"`
Expected: `ok`と出力される

- [ ] **Step 3: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_valkey_lifespan.py`を新規作成する。

```python
"""valkey_lifespan / CurrentValkeyClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentValkeyClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import pytest
from fastmcp import Client, FastMCP
from glide import GlideClient

from fastmcp_toolkit.valkey_lifespan import CurrentValkeyClient, valkey_lifespan


class FakeGlideClient:
    def __init__(self) -> None:
        self.closed = False
        self._store: dict[str, str] = {}

    async def close(self) -> None:
        self.closed = True

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


@pytest.mark.asyncio
async def test_tool_resolves_valkey_client_via_depends(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastMCP("test", lifespan=valkey_lifespan("cache", "localhost", 6379))

    @app.tool
    async def set_and_get(
        key: str, value: str, valkey: GlideClient = CurrentValkeyClient("cache")
    ) -> str:
        await valkey.set(key, value)
        result = await valkey.get(key)
        return result or ""

    async with Client(app) as client:
        result = await client.call_tool("set_and_get", {"key": "k", "value": "v"})

    assert result.data == "v"


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastMCP(
        "test",
        lifespan=valkey_lifespan("cache", "localhost", 6379)
        | valkey_lifespan("session", "localhost", 6380),
    )

    @app.tool
    async def compare(
        cache: GlideClient = CurrentValkeyClient("cache"),
        session: GlideClient = CurrentValkeyClient("session"),
    ) -> bool:
        # objectとしての別物性だけでなく、"cache"への書き込みが"session"側の
        # キーには見えないこと（名前ごとに正しくキー分けされていること）も
        # 検証する。
        await cache.set("k", "cache-value")
        session_value = await session.get("k")
        return cache is not session and session_value is None

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(valkey: GlideClient = CurrentValkeyClient("cache")) -> str:
        return type(valkey).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージ（"valkey_clients['cache']
        # is not set..."）は失われ、クライアントに届くのはDepends解決対象の
        # パラメータ名を含む "Failed to resolve dependency '<param>' for <fn>" のみ
        # になる（fastmcp-toolkitのdb_lifespan/redis_lifespanで確認済み）。
        # そのため登録名 "cache" ではなくパラメータ名 "valkey" でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'valkey'"):
            await client.call_tool("whoami", {})


def test_get_valkey_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.valkey_lifespan import _get_valkey_client

    get_client = _get_valkey_client("cache")
    with pytest.raises(RuntimeError, match="cache"):
        get_client(SimpleNamespace(lifespan_context={}))
```

- [ ] **Step 4: テストを実行し、失敗することを確認する**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_valkey_lifespan.py -v`
Expected: `ModuleNotFoundError: No module named 'fastmcp_toolkit.valkey_lifespan'`でFAIL

- [ ] **Step 5: 実装を書く**

`fastmcp-toolkit/src/fastmcp_toolkit/valkey_lifespan.py`を新規作成する。

```python
"""FastMCPサーバー向けValkey接続lifespan統合。

Starlette向けの ``core_toolkit.valkey_lifespan.ValkeyLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[valkey]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from glide import GlideClient, GlideClientConfiguration, NodeAddress
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"valkey_client:{name}"


def valkey_lifespan(name: str, host: str, port: int = 6379, **config_kwargs: Any) -> Lifespan:
    """Valkeyクライアントのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``valkey-glide`` クライアントを生成してlifespan_contextに
    格納し、終了時にcloseする。``FastMCP(lifespan=valkey_lifespan(name, host))``
    として使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のValkeyクライアントを
    同時に登録できる。同じ``name``で複数回``valkey_lifespan(...)``を登録した場合、
    ``lifespan_context``のキーが衝突し、後から登録した方で静かに上書きされる。
    同じ名前を重複登録しないこと。

    Args:
        name: このクライアントを識別する名前。``CurrentValkeyClient`` で
            同じ名前を指定して取得する。
        host: 接続先ホスト名。
        port: 接続先ポート番号。
        config_kwargs: ``GlideClientConfiguration`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _valkey_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        config = GlideClientConfiguration(
            addresses=[NodeAddress(host, port)],
            **config_kwargs,
        )
        client = await GlideClient.create(config)
        try:
            yield {_lifespan_key(name): client}
        finally:
            await client.close()

    return _valkey_lifespan


def _get_valkey_client(name: str) -> Callable[[Context], GlideClient]:
    def get_client(ctx: Context = CurrentContext()) -> GlideClient:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=valkey_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(GlideClient, client)

    get_client.__name__ = f"get_valkey_client_{name}"
    return get_client


def CurrentValkeyClient(name: str) -> GlideClient:  # noqa: N802
    """``valkey_lifespan(name, ...)`` が生成したValkeyクライアントを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(valkey: GlideClient = CurrentValkeyClient("cache")) -> str:
            ...

    Args:
        name: ``valkey_lifespan(name=...)`` に登録した名前。

    Returns:
        GlideClient: ``Depends(...)`` でラップされた、実行時に解決される
        Valkeyクライアント。
    """
    return cast(GlideClient, Depends(_get_valkey_client(name)))
```

- [ ] **Step 6: テストを実行し、成功することを確認する**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_valkey_lifespan.py -v`
Expected: 5件すべてPASS

- [ ] **Step 7: リントとフォーマットを確認する**

Run: `cd fastmcp-toolkit && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
cd fastmcp-toolkit
git add pyproject.toml src/fastmcp_toolkit/valkey_lifespan.py tests/test_valkey_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastmcp-toolkit): add valkey lifespan / CurrentValkeyClient

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
