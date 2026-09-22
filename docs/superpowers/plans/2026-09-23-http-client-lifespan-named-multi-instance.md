# HTTPクライアント(aiohttp/httpx) lifespan名前付き化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** core-toolkit / fastapi-toolkit / fastmcp-toolkitのaiohttp/httpxクライアントlifespan管理を、DB/Redisと同じ名前付き複数インスタンスAPIに再設計し、`core-toolkit/lifespan.py`の未宣言依存バグ（`aiohttp`/`httpx`の無条件import）を修正し、bffサンプルを新APIへ移行する。

**Architecture:** DB/Redisと同じ3層構成（core-toolkitに実体、fastapi-toolkitは薄い再エクスポート、fastmcp-toolkitは独立実装）。ただしaiohttp/httpxは統一抽象を持たない2ライブラリのため、DBのSQLAlchemy一本化・RedisのRedis一本化とは異なり、`aiohttp_lifespan.py`/`httpx_lifespan.py`という2ファイルに分けて両方を維持する。`url`は必須にせず`**kwargs`経由の完全パススルー（DB/Redisと同じくtoolkit側はデフォルトを注入しない）。

**Tech Stack:** `aiohttp`/`httpx`（テストは実ネットワークI/O不要——`ClientSession`/`AsyncClient`の生成・close確認のみで、DB(sqlite in-memory)・Redis(fakeredis)のような外部依存の"fake"は不要）。

**Spec:** `docs/superpowers/specs/2026-09-23-http-client-lifespan-named-multi-instance-design.md`

## Global Constraints

- Python 3.14+
- ruffでE, F, I, UPルールをlint。core-toolkit/fastmcp-toolkitは既定line-length（88）、fastapi-toolkitと`examples/bff`は`line-length = 120`
- 公開API（`AioHttpLifespanResource`, `get_aiohttp_client`, `HttpxLifespanResource`, `get_httpx_client`, `aiohttp_lifespan`, `CurrentAiohttpClient`, `httpx_lifespan`, `CurrentHttpxClient`）には日本語・GoogleスタイルのDocstringを付ける
- 名前付き複数インスタンスAPIのみを提供する。`name`引数を省略できる「単一グローバルクライアント」方式は作らない
- `url`は受け取らない。`**client_kwargs`（`session_kwargs`/`client_kwargs`という引数名）経由で`base_url=`等を渡せるだけにする
- デフォルト値は一切注入しない完全パススルー。現行の`limit=100`等のハードコードされた接続プール設定・タイムアウトはtoolkit側から削除する
- aiohttp/httpxのどちらも継続サポートする。Union型（`aiohttp.ClientSession | httpx.AsyncClient`）は廃止し、ライブラリごとに型が確定した別々のprovider関数にする
- ファイルは`aiohttp_lifespan.py`/`httpx_lifespan.py`の2本に分割する（1ファイルにまとめない。両ライブラリのimportが両方必要になり、必要なextraだけ入れれば動くという独立installができなくなるため）
- テストは実ネットワークI/O・実サーバーを必要としない（`aiohttp.ClientSession`/`httpx.AsyncClient`の生成・close自体はI/Oを伴わない）
- 依存追加後は必ず`uv sync --all-extras`を実行してから実装・テストに進む
- 各タスクの完了時点で、そのタスクが変更したパッケージの`uv run pytest`が全件PASSであることを確認する（他パッケージへの波及——特にTask 2実行後のbffサンプルの一時的な破損——は該当タスクの担当ではなく、後続タスクで解消される。慌てて対処しないこと）

---

## Task 1: core-toolkit — `aiohttp_lifespan.py`/`httpx_lifespan.py`の新設と`lifespan.py`の縮小

**Files:**
- Modify: `core-toolkit/pyproject.toml`
- Modify: `core-toolkit/src/core_toolkit/lifespan.py`
- Create: `core-toolkit/src/core_toolkit/aiohttp_lifespan.py`
- Create: `core-toolkit/src/core_toolkit/httpx_lifespan.py`
- Test: `core-toolkit/tests/test_aiohttp_lifespan.py`
- Test: `core-toolkit/tests/test_httpx_lifespan.py`

**Interfaces:**
- Consumes: `core_toolkit.lifespan.LifespanResource`（既存、`context(self, app: Starlette) -> AbstractAsyncContextManager`を実装するABC。変更なし）
- Produces:
  - `AioHttpLifespanResource(name: str, **session_kwargs: Any)` — `LifespanResource`のサブクラス
  - `get_aiohttp_client(name: str) -> Callable[[Request], aiohttp.ClientSession]`
  - `HttpxLifespanResource(name: str, **client_kwargs: Any)` — `LifespanResource`のサブクラス
  - `get_httpx_client(name: str) -> Callable[[Request], httpx.AsyncClient]`
  - これらはTask 2（fastapi-toolkit）がそのまま再エクスポートする

- [ ] **Step 1: pyproject.tomlに`aiohttp`/`httpx` extraを追加し、devから外す**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、`redis = [...]`の直後へ以下を追加する。

```toml
aiohttp = [
    "aiohttp>=3.14.1",
]
httpx = [
    "httpx>=0.28",
]
```

`dev`から`"aiohttp>=3.14.1",`と`"httpx>=0.28",`の2行を削除する（`uv sync --all-extras`で新設extra経由で入るため）。

- [ ] **Step 2: 依存をインストール**

Run: `cd core-toolkit && uv sync --all-extras`
Expected: `aiohttp`/`httpx`が新設extra経由で解決されてインストールされる（`dev`から削除しても`--all-extras`なら変わらず入る）。

- [ ] **Step 3: 失敗するテストを書く**

`core-toolkit/tests/test_aiohttp_lifespan.py`を作成する。

```python
"""aiohttp_lifespanの統合テスト。"""

import aiohttp
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client
from core_toolkit.lifespan import create_lifespan


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_aiohttp_client_returns_registered_client():
    app = Starlette()
    lifespan = create_lifespan(AioHttpLifespanResource("backend"))

    async with lifespan(app):
        client = get_aiohttp_client("backend")(_make_request(app))
        assert isinstance(client, aiohttp.ClientSession)
        assert not client.closed


def test_get_aiohttp_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="backend"):
        get_aiohttp_client("backend")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently():
    app = Starlette()
    lifespan = create_lifespan(
        AioHttpLifespanResource("backend"),
        AioHttpLifespanResource("internal"),
    )

    async with lifespan(app):
        backend_client = get_aiohttp_client("backend")(_make_request(app))
        internal_client = get_aiohttp_client("internal")(_make_request(app))
        assert backend_client is not internal_client


@pytest.mark.asyncio
async def test_aiohttp_lifespan_resource_closes_client_on_exit():
    app = Starlette()
    lifespan = create_lifespan(AioHttpLifespanResource("backend"))

    async with lifespan(app):
        client = get_aiohttp_client("backend")(_make_request(app))
        assert not client.closed

    assert client.closed


@pytest.mark.asyncio
async def test_session_kwargs_are_passed_through():
    app = Starlette()
    lifespan = create_lifespan(
        AioHttpLifespanResource("backend", headers={"X-Test": "1"})
    )

    async with lifespan(app):
        client = get_aiohttp_client("backend")(_make_request(app))
        # toolkit側は接続プール・タイムアウト等のデフォルトを一切注入しない
        # （完全パススルー）。呼び出し側が渡したkwargsがそのまま反映されることを
        # 確認する。
        assert client.headers["X-Test"] == "1"
```

`core-toolkit/tests/test_httpx_lifespan.py`を作成する。

```python
"""httpx_lifespanの統合テスト。"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.httpx_lifespan import HttpxLifespanResource, get_httpx_client
from core_toolkit.lifespan import create_lifespan


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_httpx_client_returns_registered_client():
    app = Starlette()
    lifespan = create_lifespan(HttpxLifespanResource("backend"))

    async with lifespan(app):
        client = get_httpx_client("backend")(_make_request(app))
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed


def test_get_httpx_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="backend"):
        get_httpx_client("backend")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently():
    app = Starlette()
    lifespan = create_lifespan(
        HttpxLifespanResource("backend"),
        HttpxLifespanResource("internal"),
    )

    async with lifespan(app):
        backend_client = get_httpx_client("backend")(_make_request(app))
        internal_client = get_httpx_client("internal")(_make_request(app))
        assert backend_client is not internal_client


@pytest.mark.asyncio
async def test_httpx_lifespan_resource_closes_client_on_exit():
    app = Starlette()
    lifespan = create_lifespan(HttpxLifespanResource("backend"))

    async with lifespan(app):
        client = get_httpx_client("backend")(_make_request(app))
        assert not client.is_closed

    assert client.is_closed


@pytest.mark.asyncio
async def test_client_kwargs_are_passed_through():
    app = Starlette()
    lifespan = create_lifespan(
        HttpxLifespanResource("backend", base_url="https://example.test")
    )

    async with lifespan(app):
        client = get_httpx_client("backend")(_make_request(app))
        assert str(client.base_url) == "https://example.test"
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd core-toolkit && uv run pytest tests/test_aiohttp_lifespan.py tests/test_httpx_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core_toolkit.aiohttp_lifespan'`）

- [ ] **Step 5: `lifespan.py`を縮小し、2つの新規モジュールを実装**

`core-toolkit/src/core_toolkit/lifespan.py`から`aiohttp`/`httpx`のimportと`AioHttpLifespanResource`/`HttpxLifespanResource`/`get_http_client`を削除し、以下の内容にする（`LifespanResource`/`app_state_dependency`/`create_lifespan`のみ残す）。

```python
"""Starlette/ASGIアプリ向けのlifespanリソース管理とapp.stateアクセサ。"""

import abc
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request


class LifespanResource(abc.ABC):
    @abc.abstractmethod
    def context(self, app: Starlette) -> AbstractAsyncContextManager:
        pass


def app_state_dependency[T](attr: str, type_: type[T]) -> Callable[[Request], T]:
    """``app.state.<attr>`` を取り出すprovider関数を生成する。

    対応する ``LifespanResource`` が ``create_lifespan(...)`` に登録されておらず
    属性が存在しない場合は、原因が分かりやすい ``RuntimeError`` を送出する。
    FastAPIの ``Depends(...)`` にもそのまま渡せるが、ここではFastAPIに一切
    依存せず素の ``starlette.requests.Request`` のみを扱う。

    Args:
        attr: ``app.state`` の属性名（例: ``"redis_client"``）。
        type_: 戻り値の型。実行時の検証には使わず、型パラメータ ``T`` を
            呼び出し側の引数から静的に推論させるためだけに受け取る。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。
    """

    def get_value(request: Request) -> T:
        if not hasattr(request.app.state, attr):
            raise RuntimeError(
                f"app.state.{attr} is not set. "
                "Did you forget to register the corresponding "
                "LifespanResource in create_lifespan(...)?"
            )
        return getattr(request.app.state, attr)

    get_value.__name__ = f"get_{attr}"
    return get_value


def create_lifespan(
    *resources: LifespanResource,
) -> Callable[..., AsyncGenerator[None, Any]]:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with AsyncExitStack() as stack:
            for resource in resources:
                await stack.enter_async_context(resource.context(app))

            yield

    return lifespan
```

`core-toolkit/src/core_toolkit/aiohttp_lifespan.py`を作成する。

```python
"""Starlette/ASGIアプリ向けaiohttp ClientSession lifespan管理。

名前付きで複数のClientSessionを同時に登録・取得できる。

利用には ``core-toolkit[aiohttp]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class AioHttpLifespanResource(LifespanResource):
    """名前付きでaiohttp ClientSessionを登録するリソース。同じappに複数登録できる。

    同じ``name``で複数回登録した場合、後から登録した方で静かに上書きされる。
    同じ名前を重複登録しないこと。

    Args:
        name: このセッションを識別する名前（例: ``"backend"``）。
            ``get_aiohttp_client`` で同じ名前を指定して取得する。
        session_kwargs: ``aiohttp.ClientSession`` にそのまま渡す追加引数
            （``connector=``, ``timeout=``, ``base_url=`` 等）。デフォルトは
            一切注入しない完全パススルー。
    """

    def __init__(self, name: str, **session_kwargs: Any) -> None:
        self._name = name
        self._session_kwargs = session_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        session = aiohttp.ClientSession(**self._session_kwargs)
        clients: dict[str, aiohttp.ClientSession] = getattr(app.state, "aiohttp_clients", {})
        app.state.aiohttp_clients = {**clients, self._name: session}

        try:
            yield session
        finally:
            await session.close()


def get_aiohttp_client(name: str) -> Callable[[Request], aiohttp.ClientSession]:
    """名前を指定してaiohttp ClientSessionを取得するprovider関数を生成する。

    Args:
        name: ``AioHttpLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``AioHttpLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> aiohttp.ClientSession:
        clients: dict[str, aiohttp.ClientSession] = getattr(request.app.state, "aiohttp_clients", {})
        if name not in clients:
            raise RuntimeError(
                f"aiohttp_clients['{name}'] is not set. "
                f"Did you forget to register AioHttpLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_aiohttp_client_{name}"
    return get_client
```

`core-toolkit/src/core_toolkit/httpx_lifespan.py`を作成する。

```python
"""Starlette/ASGIアプリ向けhttpx AsyncClient lifespan管理。

名前付きで複数のAsyncClientを同時に登録・取得できる。

利用には ``core-toolkit[httpx]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class HttpxLifespanResource(LifespanResource):
    """名前付きでhttpx AsyncClientを登録するリソース。同じappに複数登録できる。

    同じ``name``で複数回登録した場合、後から登録した方で静かに上書きされる。
    同じ名前を重複登録しないこと。

    Args:
        name: このクライアントを識別する名前（例: ``"backend"``）。
            ``get_httpx_client`` で同じ名前を指定して取得する。
        client_kwargs: ``httpx.AsyncClient`` にそのまま渡す追加引数
            （``timeout=``, ``limits=``, ``base_url=`` 等）。デフォルトは
            一切注入しない完全パススルー。
    """

    def __init__(self, name: str, **client_kwargs: Any) -> None:
        self._name = name
        self._client_kwargs = client_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        client = httpx.AsyncClient(**self._client_kwargs)
        clients: dict[str, httpx.AsyncClient] = getattr(app.state, "httpx_clients", {})
        app.state.httpx_clients = {**clients, self._name: client}

        try:
            yield client
        finally:
            await client.aclose()


def get_httpx_client(name: str) -> Callable[[Request], httpx.AsyncClient]:
    """名前を指定してhttpx AsyncClientを取得するprovider関数を生成する。

    Args:
        name: ``HttpxLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``HttpxLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> httpx.AsyncClient:
        clients: dict[str, httpx.AsyncClient] = getattr(request.app.state, "httpx_clients", {})
        if name not in clients:
            raise RuntimeError(
                f"httpx_clients['{name}'] is not set. "
                f"Did you forget to register HttpxLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_httpx_client_{name}"
    return get_client
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd core-toolkit && uv run pytest tests/test_aiohttp_lifespan.py tests/test_httpx_lifespan.py -v`
Expected: PASS（各5件、計10件）

- [ ] **Step 7: core-toolkit全体のテストを実行し既存分の非破壊を確認**

Run: `cd core-toolkit && uv run pytest -v`
Expected: 全件PASS（新規10件 + 既存144件 = 154件）。`core_toolkit/db_lifespan.py`/`redis_lifespan.py`は`LifespanResource`のみを使っており今回の変更の影響を受けない

- [ ] **Step 8: lint/formatを実行**

Run: `cd core-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 9: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/uv.lock core-toolkit/src/core_toolkit/lifespan.py core-toolkit/src/core_toolkit/aiohttp_lifespan.py core-toolkit/src/core_toolkit/httpx_lifespan.py core-toolkit/tests/test_aiohttp_lifespan.py core-toolkit/tests/test_httpx_lifespan.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add named multi-instance aiohttp/httpx lifespan

lifespan.pyからaiohttp/httpxの無条件importを取り除き（未宣言依存バグの
修正）、AioHttpLifespanResource/HttpxLifespanResourceをそれぞれ独立した
aiohttp_lifespan.py/httpx_lifespan.pyへ分離。db_lifespan/redis_lifespanと
同じ名前付き複数インスタンスAPIとし、接続プール・タイムアウトの
ハードコードされたデフォルトは廃止して完全パススルーにした。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: fastapi-toolkit — `lifespan.py`の再エクスポート化と`aiohttp_lifespan.py`/`httpx_lifespan.py`薄いラッパー

**Files:**
- Modify: `fastapi-toolkit/pyproject.toml`
- Modify: `fastapi-toolkit/src/fastapi_toolkit/lifespan.py`
- Create: `fastapi-toolkit/src/fastapi_toolkit/aiohttp_lifespan.py`
- Create: `fastapi-toolkit/src/fastapi_toolkit/httpx_lifespan.py`
- Test: `fastapi-toolkit/tests/test_aiohttp_lifespan.py`
- Test: `fastapi-toolkit/tests/test_httpx_lifespan.py`

**Interfaces:**
- Consumes: Task 1の`core_toolkit.lifespan.{LifespanResource,create_lifespan,app_state_dependency}`、`core_toolkit.aiohttp_lifespan.{AioHttpLifespanResource,get_aiohttp_client}`、`core_toolkit.httpx_lifespan.{HttpxLifespanResource,get_httpx_client}`（シグネチャはTask 1のProducesを参照）
- Produces: `fastapi_toolkit.lifespan.{LifespanResource,create_lifespan,app_state_dependency}`（再エクスポートのみ）、`fastapi_toolkit.aiohttp_lifespan.{AioHttpLifespanResource,get_aiohttp_client}`、`fastapi_toolkit.httpx_lifespan.{HttpxLifespanResource,get_httpx_client}`（Task 1のものをそのまま再エクスポート、シグネチャ変更なし）。Task 4が使う側

**注意:** このタスク完了後、`fastapi-toolkit/examples/bff`は一時的にimportエラーで壊れる（`bff/app.py`が旧`fastapi_toolkit.lifespan.AioHttpLifespanResource`をimportしているため）。これはTask 4で解消される想定であり、このタスクの担当ではない。`fastapi-toolkit`自体の`uv run pytest`には影響しない（bffは別のuvワークスペースメンバーで、fastapi-toolkitのテストスイートには含まれない）。

- [ ] **Step 1: pyproject.tomlの依存を整理**

`fastapi-toolkit/pyproject.toml`の`dependencies`から`"aiohttp>=3.14.1",`を削除する（現状: `["aiohttp>=3.14.1", "core-toolkit", "fastapi>=0.115", "structlog>=24.0"]` → `["core-toolkit", "fastapi>=0.115", "structlog>=24.0"]`）。fastapi-toolkit自体のソースコードは今後aiohttpを直接使わなくなる（`lifespan.py`の縮小後、aiohttp/httpxを直接importするのは新設2ファイルのみで、どちらも`core-toolkit`側のextra経由で解決する）。

`[project.optional-dependencies]`に、既存の`redis = [...]`の直後へ以下を追加する。

```toml
aiohttp = [
  "core-toolkit[aiohttp]",
]
httpx = [
  "core-toolkit[httpx]",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd fastapi-toolkit && uv sync --all-extras`
Expected: core-toolkitの`aiohttp`/`httpx`エクストラ経由で解決される。

- [ ] **Step 3: 失敗するテストを書く**

`fastapi-toolkit/tests/test_aiohttp_lifespan.py`を作成する。

```python
"""AioHttpLifespanResource/get_aiohttp_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

AioHttpLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_aiohttp_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import aiohttp
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client
from fastapi_toolkit.lifespan import create_lifespan

BackendClient = Annotated[aiohttp.ClientSession, Depends(get_aiohttp_client("backend"))]


@pytest.mark.asyncio
async def test_aiohttp_client_resolves_via_depends():
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(session: BackendClient) -> dict[str, bool]:
        return {"closed": session.closed}

    lifespan = create_lifespan(AioHttpLifespanResource("backend"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/whoami")

    assert resp.status_code == 200
    assert resp.json() == {"closed": False}
```

`fastapi-toolkit/tests/test_httpx_lifespan.py`を作成する。

```python
"""HttpxLifespanResource/get_httpx_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

HttpxLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_httpx_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.httpx_lifespan import HttpxLifespanResource, get_httpx_client
from fastapi_toolkit.lifespan import create_lifespan

BackendClient = Annotated[httpx.AsyncClient, Depends(get_httpx_client("backend"))]


@pytest.mark.asyncio
async def test_httpx_client_resolves_via_depends():
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(client: BackendClient) -> dict[str, bool]:
        return {"closed": client.is_closed}

    lifespan = create_lifespan(HttpxLifespanResource("backend"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/whoami")

    assert resp.status_code == 200
    assert resp.json() == {"closed": False}
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_aiohttp_lifespan.py tests/test_httpx_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastapi_toolkit.aiohttp_lifespan'`）

- [ ] **Step 5: `lifespan.py`を再エクスポート化し、2つの薄いラッパーを実装**

`fastapi-toolkit/src/fastapi_toolkit/lifespan.py`を以下に置き換える。

```python
"""lifespanリソース管理のFastAPI向け薄いラッパー。

``LifespanResource``/``create_lifespan``/``app_state_dependency``は
FastAPIに依存せず ``core_toolkit.lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけ。
"""

from core_toolkit.lifespan import LifespanResource, app_state_dependency, create_lifespan

__all__ = ["LifespanResource", "app_state_dependency", "create_lifespan"]
```

`fastapi-toolkit/src/fastapi_toolkit/aiohttp_lifespan.py`を作成する。

```python
"""aiohttp ClientSession lifespanリソースのFastAPI向け薄いラッパー。

``AioHttpLifespanResource``/``get_aiohttp_client`` はFastAPIに依存せず
``core_toolkit.aiohttp_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[aiohttp.ClientSession, Depends(get_aiohttp_client("backend"))]``）
はアプリ側が名前を指定して定義する。

利用には ``fastapi-toolkit[aiohttp]`` extraのインストールが必要。
"""

from core_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client

__all__ = ["AioHttpLifespanResource", "get_aiohttp_client"]
```

`fastapi-toolkit/src/fastapi_toolkit/httpx_lifespan.py`を作成する。

```python
"""httpx AsyncClient lifespanリソースのFastAPI向け薄いラッパー。

``HttpxLifespanResource``/``get_httpx_client`` はFastAPIに依存せず
``core_toolkit.httpx_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[httpx.AsyncClient, Depends(get_httpx_client("backend"))]``）
はアプリ側が名前を指定して定義する。

利用には ``fastapi-toolkit[httpx]`` extraのインストールが必要。
"""

from core_toolkit.httpx_lifespan import HttpxLifespanResource, get_httpx_client

__all__ = ["HttpxLifespanResource", "get_httpx_client"]
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_aiohttp_lifespan.py tests/test_httpx_lifespan.py -v`
Expected: PASS（各1件、計2件）

- [ ] **Step 7: fastapi-toolkit全体のテストを実行し既存分の非破壊を確認**

Run: `cd fastapi-toolkit && uv run pytest -v`
Expected: 全件PASS（新規2件 + 既存9件 = 11件）

- [ ] **Step 8: lint/formatを実行**

Run: `cd fastapi-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 9: コミット**

```bash
git add fastapi-toolkit/pyproject.toml fastapi-toolkit/uv.lock fastapi-toolkit/src/fastapi_toolkit/lifespan.py fastapi-toolkit/src/fastapi_toolkit/aiohttp_lifespan.py fastapi-toolkit/src/fastapi_toolkit/httpx_lifespan.py fastapi-toolkit/tests/test_aiohttp_lifespan.py fastapi-toolkit/tests/test_httpx_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastapi-toolkit): re-export named multi-instance aiohttp/httpx lifespan

lifespan.pyをcore_toolkit.lifespanの薄い再エクスポートに整理し
（db_lifespan/redis_lifespanと同じ形に統一）、AioHttpLifespanResource/
HttpxLifespanResourceは独立したaiohttp_lifespan.py/httpx_lifespan.pyへ
分離した。aiohttpはfastapi-toolkit自体の直接依存からも外し、extra経由に
した。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: fastmcp-toolkit — `aiohttp_lifespan.py`/`httpx_lifespan.py`独立実装

**Files:**
- Modify: `fastmcp-toolkit/pyproject.toml`
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/aiohttp_lifespan.py`
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/httpx_lifespan.py`
- Test: `fastmcp-toolkit/tests/test_aiohttp_lifespan.py`
- Test: `fastmcp-toolkit/tests/test_httpx_lifespan.py`

**Interfaces:**
- Consumes: `fastmcp.server.dependencies.CurrentContext`（既存）、`fastmcp.server.lifespan.lifespan`/`Lifespan`（既存、`db_lifespan.py`/`redis_lifespan.py`と同じ使い方）、`uncalled_for.Depends`（既存）
- Produces:
  - `aiohttp_lifespan(name: str, **session_kwargs: Any) -> Lifespan`
  - `_get_aiohttp_client(name: str) -> Callable[[Context], aiohttp.ClientSession]`（モジュール内部用）
  - `CurrentAiohttpClient(name: str) -> aiohttp.ClientSession`
  - `httpx_lifespan(name: str, **client_kwargs: Any) -> Lifespan`
  - `_get_httpx_client(name: str) -> Callable[[Context], httpx.AsyncClient]`（モジュール内部用）
  - `CurrentHttpxClient(name: str) -> httpx.AsyncClient`
  - 現時点でこれらを使うexampleは作らない（specの「未決事項」節参照。将来fastmcp向けのHTTP呼び出しサンプルが必要になったら別途追加する）

- [ ] **Step 1: pyproject.tomlに`aiohttp`/`httpx` extraを追加**

`fastmcp-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis = [...]`の直後へ以下を追加する。

```toml
aiohttp = [
    "aiohttp>=3.14.1",
]
httpx = [
    "httpx>=0.28",
]
```

（`dev`に既存の`"httpx>=0.27",`はASGIテストクライアント用途で今回の機能とは無関係のため、そのまま残す）

- [ ] **Step 2: 依存をインストール**

Run: `cd fastmcp-toolkit && uv sync --all-extras`
Expected: `aiohttp`/`httpx`が新設extra経由で解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_aiohttp_lifespan.py`を作成する。

```python
"""aiohttp_lifespan / CurrentAiohttpClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentAiohttpClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import aiohttp
import pytest
from fastmcp import Client, FastMCP

from fastmcp_toolkit.aiohttp_lifespan import CurrentAiohttpClient, aiohttp_lifespan


@pytest.mark.asyncio
async def test_tool_resolves_aiohttp_client_via_depends():
    app = FastMCP("test", lifespan=aiohttp_lifespan("backend"))

    @app.tool
    async def whoami(session: aiohttp.ClientSession = CurrentAiohttpClient("backend")) -> bool:
        return session.closed

    async with Client(app) as client:
        result = await client.call_tool("whoami", {})

    assert result.data is False


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent():
    app = FastMCP(
        "test",
        lifespan=aiohttp_lifespan("backend") | aiohttp_lifespan("internal"),
    )

    @app.tool
    async def compare(
        backend: aiohttp.ClientSession = CurrentAiohttpClient("backend"),
        internal: aiohttp.ClientSession = CurrentAiohttpClient("internal"),
    ) -> bool:
        return backend is not internal

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(session: aiohttp.ClientSession = CurrentAiohttpClient("backend")) -> str:
        return type(session).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージは失われ、クライアント
        # に届くのはDepends解決対象のパラメータ名を含む"Failed to resolve
        # dependency '<param>' for <fn>"のみになる（db_lifespanのa9b2fb9参照）。
        # そのため登録名"backend"ではなくパラメータ名"session"でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'session'"):
            await client.call_tool("whoami", {})


def test_get_aiohttp_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.aiohttp_lifespan import _get_aiohttp_client

    get_client = _get_aiohttp_client("backend")
    with pytest.raises(RuntimeError, match="backend"):
        get_client(SimpleNamespace(lifespan_context={}))
```

`fastmcp-toolkit/tests/test_httpx_lifespan.py`を作成する。

```python
"""httpx_lifespan / CurrentHttpxClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentHttpxClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import httpx
import pytest
from fastmcp import Client, FastMCP

from fastmcp_toolkit.httpx_lifespan import CurrentHttpxClient, httpx_lifespan


@pytest.mark.asyncio
async def test_tool_resolves_httpx_client_via_depends():
    app = FastMCP("test", lifespan=httpx_lifespan("backend"))

    @app.tool
    async def whoami(client: httpx.AsyncClient = CurrentHttpxClient("backend")) -> bool:
        return client.is_closed

    async with Client(app) as client:
        result = await client.call_tool("whoami", {})

    assert result.data is False


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent():
    app = FastMCP(
        "test",
        lifespan=httpx_lifespan("backend") | httpx_lifespan("internal"),
    )

    @app.tool
    async def compare(
        backend: httpx.AsyncClient = CurrentHttpxClient("backend"),
        internal: httpx.AsyncClient = CurrentHttpxClient("internal"),
    ) -> bool:
        return backend is not internal

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(client: httpx.AsyncClient = CurrentHttpxClient("backend")) -> str:
        return type(client).__name__

    async with Client(app) as client:
        # test_aiohttp_lifespan.pyの同名テストと同じ理由（a9b2fb9参照）で、
        # 登録名"backend"ではなくパラメータ名"client"でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'client'"):
            await client.call_tool("whoami", {})


def test_get_httpx_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.httpx_lifespan import _get_httpx_client

    get_client = _get_httpx_client("backend")
    with pytest.raises(RuntimeError, match="backend"):
        get_client(SimpleNamespace(lifespan_context={}))
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_aiohttp_lifespan.py tests/test_httpx_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastmcp_toolkit.aiohttp_lifespan'`）

- [ ] **Step 5: 実装を書く**

`fastmcp-toolkit/src/fastmcp_toolkit/aiohttp_lifespan.py`を作成する。

```python
"""FastMCPサーバー向けaiohttp ClientSession lifespan統合。

Starlette向けの ``core_toolkit.aiohttp_lifespan.AioHttpLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[aiohttp]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import aiohttp
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"aiohttp_client:{name}"


def aiohttp_lifespan(name: str, **session_kwargs: Any) -> Lifespan:
    """aiohttp ClientSessionのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``aiohttp.ClientSession`` を生成してlifespan_contextに格納し、
    終了時にcloseする。``FastMCP(lifespan=aiohttp_lifespan(name, ...))`` として
    使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のセッションを同時に登録
    できる。同じ``name``で複数回登録した場合、``lifespan_context``のキーが
    衝突し、後から登録した方で静かに上書きされる。同じ名前を重複登録しない
    こと。

    Args:
        name: このセッションを識別する名前。``CurrentAiohttpClient`` で
            同じ名前を指定して取得する。
        session_kwargs: ``aiohttp.ClientSession`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _aiohttp_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        session = aiohttp.ClientSession(**session_kwargs)
        try:
            yield {_lifespan_key(name): session}
        finally:
            await session.close()

    return _aiohttp_lifespan


def _get_aiohttp_client(name: str) -> Callable[[Context], aiohttp.ClientSession]:
    def get_client(ctx: Context = CurrentContext()) -> aiohttp.ClientSession:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=aiohttp_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(aiohttp.ClientSession, client)

    get_client.__name__ = f"get_aiohttp_client_{name}"
    return get_client


def CurrentAiohttpClient(name: str) -> aiohttp.ClientSession:
    """``aiohttp_lifespan(name, ...)`` が生成したClientSessionを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(session: aiohttp.ClientSession = CurrentAiohttpClient("backend")) -> str:
            ...

    Args:
        name: ``aiohttp_lifespan(name=...)`` に登録した名前。

    Returns:
        aiohttp.ClientSession: ``Depends(...)`` でラップされた、実行時に
        解決されるClientSession。
    """
    return cast(aiohttp.ClientSession, Depends(_get_aiohttp_client(name)))
```

`fastmcp-toolkit/src/fastmcp_toolkit/httpx_lifespan.py`を作成する。

```python
"""FastMCPサーバー向けhttpx AsyncClient lifespan統合。

Starlette向けの ``core_toolkit.httpx_lifespan.HttpxLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[httpx]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"httpx_client:{name}"


def httpx_lifespan(name: str, **client_kwargs: Any) -> Lifespan:
    """httpx AsyncClientのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``httpx.AsyncClient`` を生成してlifespan_contextに格納し、
    終了時にacloseする。``FastMCP(lifespan=httpx_lifespan(name, ...))`` として
    使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のクライアントを同時に登録
    できる。同じ``name``で複数回登録した場合、``lifespan_context``のキーが
    衝突し、後から登録した方で静かに上書きされる。同じ名前を重複登録しない
    こと。

    Args:
        name: このクライアントを識別する名前。``CurrentHttpxClient`` で
            同じ名前を指定して取得する。
        client_kwargs: ``httpx.AsyncClient`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _httpx_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        client = httpx.AsyncClient(**client_kwargs)
        try:
            yield {_lifespan_key(name): client}
        finally:
            await client.aclose()

    return _httpx_lifespan


def _get_httpx_client(name: str) -> Callable[[Context], httpx.AsyncClient]:
    def get_client(ctx: Context = CurrentContext()) -> httpx.AsyncClient:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=httpx_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(httpx.AsyncClient, client)

    get_client.__name__ = f"get_httpx_client_{name}"
    return get_client


def CurrentHttpxClient(name: str) -> httpx.AsyncClient:
    """``httpx_lifespan(name, ...)`` が生成したAsyncClientを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(client: httpx.AsyncClient = CurrentHttpxClient("backend")) -> str:
            ...

    Args:
        name: ``httpx_lifespan(name=...)`` に登録した名前。

    Returns:
        httpx.AsyncClient: ``Depends(...)`` でラップされた、実行時に
        解決されるAsyncClient。
    """
    return cast(httpx.AsyncClient, Depends(_get_httpx_client(name)))
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_aiohttp_lifespan.py tests/test_httpx_lifespan.py -v`
Expected: PASS（各4件、計8件）

- [ ] **Step 7: fastmcp-toolkit全体のテストを実行し既存分の非破壊を確認**

Run: `cd fastmcp-toolkit && uv run pytest -v`
Expected: 全件PASS（新規8件 + 既存108件 = 116件）

- [ ] **Step 8: lint/formatを実行**

Run: `cd fastmcp-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 9: コミット**

```bash
git add fastmcp-toolkit/pyproject.toml fastmcp-toolkit/uv.lock fastmcp-toolkit/src/fastmcp_toolkit/aiohttp_lifespan.py fastmcp-toolkit/src/fastmcp_toolkit/httpx_lifespan.py fastmcp-toolkit/tests/test_aiohttp_lifespan.py fastmcp-toolkit/tests/test_httpx_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastmcp-toolkit): add named multi-instance aiohttp/httpx lifespan

lifespan_contextベースの独立実装でaiohttp_lifespan/CurrentAiohttpClientと
httpx_lifespan/CurrentHttpxClientを追加。RPC境界越しの「未登録」テストは
Dependsパラメータ名でmatchする（db_lifespanのa9b2fb9と同じ理由）。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: fastapi-toolkit — bffサンプルを名前付きaiohttpクライアントへ移行

**Files:**
- Modify: `fastapi-toolkit/examples/bff/src/bff/app.py`
- Test: `fastapi-toolkit/examples/bff/tests/test_backend.py`

**Interfaces:**
- Consumes: Task 2の`fastapi_toolkit.aiohttp_lifespan.{AioHttpLifespanResource,get_aiohttp_client}`、`fastapi_toolkit.lifespan.create_lifespan`（既存）
- Produces: `bff.app.get_backend_http_client`（テストが`dependency_overrides`のキーとして使う）。他タスクからは参照されない（末端のサンプル）

**設計判断:** 旧`AioHttpLifespanResource()`（引数なし）は`backend_a_url`/`backend_b_url`という別々のバックエンドに単一の共有クライアントを使い回していた。この移行では両者を1つの名前付きクライアント（`"backend"`）にまとめ、既存の呼び出し方（絶対URLを`session.get(url)`に渡す）を変えない最小差分の移行とする（別クライアントへの分割は必要になった時点で個別に検討する、というspecの「未決事項」節の判断をここで確定させる）。登録名と解決名の食い違いを防ぐため、文字列リテラル`"backend"`はモジュール内で1箇所にしか書かない設計にする。

- [ ] **Step 1: 失敗するテストを書く**

`fastapi-toolkit/examples/bff/tests/test_backend.py`を作成する。

```python
"""backend_a/backend_b/backend_b_cエンドポイントの結合テスト。

aiohttp ClientSessionを``dependency_overrides``でフェイクに差し替え、実際の
バックエンドサービスには接続しない。
"""

from httpx import ASGITransport, AsyncClient


class _FakeAiohttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload

    async def __aenter__(self) -> "_FakeAiohttpResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeAiohttpSession:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.requested_urls: list[str] = []

    def get(self, url: str, **kwargs: object) -> _FakeAiohttpResponse:
        self.requested_urls.append(url)
        return _FakeAiohttpResponse(self._payload)


async def test_backend_a_returns_json_from_configured_url():
    from bff.app import app, get_backend_http_client

    fake_session = _FakeAiohttpSession({"result": "a"})
    app.dependency_overrides[get_backend_http_client] = lambda: fake_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/backend/a")
    finally:
        app.dependency_overrides.pop(get_backend_http_client, None)

    assert resp.status_code == 200
    assert resp.json() == {"result": "a"}
    assert fake_session.requested_urls == ["http://localhost:8001/api/a"]


async def test_backend_b_c_returns_json_from_configured_url():
    from bff.app import app, get_backend_http_client

    fake_session = _FakeAiohttpSession({"result": "c"})
    app.dependency_overrides[get_backend_http_client] = lambda: fake_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/backend/b/c")
    finally:
        app.dependency_overrides.pop(get_backend_http_client, None)

    assert resp.status_code == 200
    assert resp.json() == {"result": "c"}
    assert fake_session.requested_urls == ["http://localhost:8002/api/b/c"]
```

- [ ] **Step 2: テストを実行し失敗を確認**

Run: `cd fastapi-toolkit/examples/bff && uv run pytest tests/test_backend.py -v`
Expected: FAIL（`ImportError: cannot import name 'get_backend_http_client' from 'bff.app'`、または`ModuleNotFoundError: No module named 'fastapi_toolkit.aiohttp_lifespan'`——Task 2完了後であれば前者）

- [ ] **Step 3: `app.py`を書き換える**

`fastapi-toolkit/examples/bff/src/bff/app.py`を以下に置き換える。

```python
"""FastAPIアプリケーション定義。"""

from typing import Annotated

import aiohttp
import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_toolkit import setup_logging
from fastapi_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client
from fastapi_toolkit.lifespan import create_lifespan
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

_BACKEND_CLIENT_NAME = "backend"
get_backend_http_client = get_aiohttp_client(_BACKEND_CLIENT_NAME)
BackendHttpClient = Annotated[aiohttp.ClientSession, Depends(get_backend_http_client)]

app = FastAPI(
    title="BFF",
    lifespan=create_lifespan(
        AioHttpLifespanResource(
            _BACKEND_CLIENT_NAME,
            connector=aiohttp.TCPConnector(
                limit=100,
                limit_per_host=20,
                use_dns_cache=True,
                ttl_dns_cache=0,
            ),
            timeout=aiohttp.ClientTimeout(total=30, connect=10, sock_connect=5, sock_read=5),
        ),
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
async def backend_a(session: BackendHttpClient) -> dict[str, str]:
    """バックエンドAPI Aにアクセスする"""
    async with session.get(settings.backend_a_url) as response:
        return await response.json()


@app.get("/backend/b")
async def backend_b(req: Request, session: BackendHttpClient) -> JSONResponse:
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

    async with session.get(
        settings.backend_b_url,
        headers={"Authorization": f"Bearer {access_token}"},
    ) as response:
        data = await response.json()
    return JSONResponse(data)


@app.get("/backend/b/c")
async def backend_b_c(session: BackendHttpClient) -> dict[str, str]:
    """バックエンドAPI BのCエンドポイントにアクセスする"""
    async with session.get(settings.backend_b_url + "/c") as response:
        return await response.json()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_config=None)
```

（変更点は import の入れ替え、`_BACKEND_CLIENT_NAME`/`get_backend_http_client`/`BackendHttpClient`の追加、`AioHttpLifespanResource`の呼び出しに名前と旧デフォルト相当のkwargsを明示、`/backend/a`・`/backend/b`・`/backend/b/c`が`req.app.state.http_client`ではなく`session: BackendHttpClient`引数を受け取る点のみ。認証ロジック・`/hello`・cache機能は変更なし）

- [ ] **Step 4: テストを実行し成功を確認**

Run: `cd fastapi-toolkit/examples/bff && uv run pytest tests/test_backend.py -v`
Expected: PASS（2件）

- [ ] **Step 5: 既存テストが壊れていないことを確認**

Run: `cd fastapi-toolkit/examples/bff && uv run pytest -v`
Expected: 全件PASS（新規2件 + 既存9件 = 11件。`test_auth.py`/`test_hello.py`/`tests/cache/`含む）

- [ ] **Step 6: lint/formatを実行**

Run: `cd fastapi-toolkit/examples/bff && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add fastapi-toolkit/examples/bff/src/bff/app.py fastapi-toolkit/examples/bff/tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(fastapi-toolkit): migrate bff sample to named aiohttp lifespan

backend_a/backend_bが共有していた単一グローバルhttp_clientを、名前付き
AioHttpLifespanResource("backend", ...)とDepends(get_aiohttp_client(...))
チェーンへ移行した。登録名/解決名の食い違いを防ぐため文字列リテラルは
モジュール内で1箇所にまとめた。これまでテストの無かったbackend_a/
backend_b_cエンドポイントに結合テストを追加した。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 自己レビューで確認した点

- **spec網羅性**: specの「意思決定サマリー」全6項目（aiohttp/httpxの扱い/`url`引数/デフォルト値/ファイル配置/未宣言依存の修正/bffサンプル）は全タスクのコード・extras・テストに反映済み。「非スコープ」節の各項目（1本化・デフォルト値のtoolkit残置・backend_a/backend_b分割の最終判断）は本計画のTask 4で「分割しない」という形で確定させ、意図的にどのタスクにも1本化やデフォルト値注入を含めていない
- **プレースホルダ**: 全コードブロックは実際に動く完全な内容（`TODO`等なし）
- **型/シグネチャの一貫性**: `AioHttpLifespanResource(name, **kwargs)`/`get_aiohttp_client(name)`/`HttpxLifespanResource(name, **kwargs)`/`get_httpx_client(name)`の引数・戻り値の型はTask 1〜3で統一。fastmcp版の`aiohttp_lifespan(name, **kwargs)`/`CurrentAiohttpClient(name)`も同型。Task 2はTask 1のシグネチャをそのまま再エクスポートするだけで変更していない。Task 4はTask 2のAPIをそのまま呼び出すだけで新たなシグネチャを追加していない
- **未宣言依存バグの解消**: core-toolkitの`aiohttp`/`httpx`は新設extra経由でのみ解決されるようになり、`core_toolkit/lifespan.py`（縮小後）はどちらも無条件importしない。fastapi-toolkitも自身の`dependencies`から`aiohttp`を外し、extra経由に統一した
- **bffの登録名/解決名の食い違い対策**: Redis機能の最終レビューで見つかった「登録名と解決名が一致していることを検証するテストが無い」という指摘を踏まえ、Task 4では文字列リテラル`"backend"`を`_BACKEND_CLIENT_NAME`という単一の変数にまとめることで、そもそもタイプミスによる不一致が起こり得ない設計にした
- **fastmcp-toolkitのexamplesテスト実行方法**: `testpaths`は既にRedis作業で`["tests", "examples"]`に更新済みのため、Task 3では追加のpyproject変更は不要（このタスクではexampleを新設しないため対象外でもある）
