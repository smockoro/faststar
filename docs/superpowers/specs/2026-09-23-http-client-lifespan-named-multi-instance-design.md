# HTTPクライアント(aiohttp/httpx) lifespan部品 名前付き化 設計

- 日付: 2026-09-23
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit` / `fastapi-toolkit/examples/bff`
- ステータス: ドラフト（ユーザーレビュー待ち）

## 背景・目的

faststarには既に`AioHttpLifespanResource`/`HttpxLifespanResource`（core-toolkit）というHTTPクライアントのlifespan管理部品があるが、DB接続部品・Redis接続部品で確立した設計（`(name, url/なし, **kwargs)`の名前付き複数インスタンスAPI、core-toolkitに実体・fastapi-toolkitは薄い再エクスポート・fastmcp-toolkitは独立実装という3層配置）から取り残されている。

具体的な問題:
- `AioHttpLifespanResource`/`HttpxLifespanResource`はどちらも`app.state.http_client`という同一キーに書き込むため、同時に2つ登録できない
- コンストラクタが引数を取らず、接続プール数・タイムアウトが`AioHttpLifespanResource`内にハードコードされている。`HttpxLifespanResource`は設定の余地がゼロ
- `fastapi_toolkit/lifespan.py`は`core_toolkit.lifespan`と同じクラスをまるごと再定義しており、DB/Redisで整理済みの「薄い再エクスポート」パターンになっていない
- fastmcp-toolkitにはHTTPクライアントのlifespan統合が一切存在しない
- `core_toolkit/lifespan.py`は`aiohttp`/`httpx`を無条件importしているが、`pyproject.toml`の`dependencies`にはどちらも入っておらず`dev` extraにのみ存在する未宣言依存になっている（`core_toolkit/health.py`のstarlette未宣言依存で過去に見つかったのと同じパターンのバグ）
- `core-toolkit`/`fastapi-toolkit`ともにHTTPクライアント部分を検証するテストが存在しない
- 実利用例（bffサンプル）は`backend_a_url`/`backend_b_url`という別サービスに単一グローバルクライアントを使い回しており、`Depends(get_http_client)`も使わず`req.app.state.http_client`への直接アクセスになっている

## スコープ

- aiohttp/httpxクライアントのlifespan管理を、DB/Redisと同型の名前付き複数インスタンスAPIに再設計する
- core-toolkit/fastapi-toolkit/fastmcp-toolkitの3層への配置
- `core_toolkit/health.py`と同種だった未宣言依存バグの修正（`aiohttp`/`httpx`をそれぞれ独立したextraにする）
- bffサンプルを新APIへ移行し、`Depends`チェーン経由でのアクセスに統一する
- 上記のテスト整備

## 非スコープ（意図的に含めない）

- aiohttp/httpxのどちらか一方への統合 — 両方を引き続きサポートする方針で確定済み（DBのSQLAlchemy一本化とは異なる判断）
- `url`/`base_url`を必須引数にすること — HTTPクライアントは呼び出し時にURLを指定する汎用クライアントであり、DB/Redisのように「1接続先に固定される」ものではないため、`url`は受け取らず`**client_kwargs`経由の任意項目（`base_url=`）とする
- 接続プール・タイムアウトのデフォルト値をtoolkit側に残すこと — DB/Redisと同じ完全パススルー方針とし、現行の`limit=100`等のハードコードされたデフォルトは廃止する（呼び出し側が必要なら`client_kwargs`で明示的に指定する）
- bffの`backend_a`/`backend_b`を別クライアントに分割するかどうかの最終判断 — 実装計画（writing-plans）時に改めて確認する

## 意思決定サマリー

| 論点 | 決定 | 理由 |
|---|---|---|
| aiohttp/httpxの扱い | 両方継続、ライブラリごとに別クラス・別provider（`get_aiohttp_client(name)`/`get_httpx_client(name)`）、Union型は廃止 | DB/Redisと異なり統一抽象が存在する2ライブラリではないため、無理に1本化せず型を確定させる方を優先 |
| `url`引数 | 必須にしない。`**client_kwargs`経由で`base_url=`を渡せるだけ | HTTPクライアントは呼び出し時にURLを指定する汎用クライアントであり、DB/Redisの「1接続先固定」とは性質が違う |
| デフォルト値 | 完全パススルー。プール数・タイムアウトのハードコードは廃止 | DB(`create_async_engine(url, **kwargs)`)・Redis(`Redis.from_url(url, **kwargs)`)と同じ「toolkitはデフォルトを注入しない」方針に統一 |
| ファイル配置 | `http_lifespan.py`1本ではなく、`aiohttp_lifespan.py`/`httpx_lifespan.py`の2ファイルに分割 | 1ファイルにまとめると両ライブラリのimportが両方必要になり、「必要なextraだけ入れれば動く」というDBのsqlite/postgres/mysql分割と同じ独立installができなくなるため |
| 未宣言依存の修正 | `core-toolkit[aiohttp]`/`core-toolkit[httpx]`という独立extraを新設し、`dev`からは外す | `core_toolkit/lifespan.py`が`aiohttp`/`httpx`を無条件importしているのに`dependencies`にも`dev`以外のextraにも入っていない状態は、過去に見つかった`core_toolkit/health.py`のstarlette未宣言依存と同じ欠陥のため |
| bffサンプル | `req.app.state.http_client`直接アクセスをやめ、`Depends(get_aiohttp_client(name))`型エイリアス経由に統一 | DB/Redisで確立したDependsチェーンの流儀に合わせる |

## アーキテクチャ

### レイヤー構造（DB/Redisと同型、ただしライブラリごとに2ファイル）

```
core-toolkit/src/core_toolkit/aiohttp_lifespan.py
  - AioHttpLifespanResource（LifespanResourceのサブクラス、名前付き複数登録対応）
  - get_aiohttp_client(name)   … provider関数生成
  - aiohttpにのみ依存

core-toolkit/src/core_toolkit/httpx_lifespan.py
  - HttpxLifespanResource / get_httpx_client(name)
  - httpxにのみ依存

fastapi-toolkit/src/fastapi_toolkit/{aiohttp,httpx}_lifespan.py
  - 上記をそれぞれ再エクスポートするだけの薄いラッパー

fastmcp-toolkit/src/fastmcp_toolkit/{aiohttp,httpx}_lifespan.py
  - lifespan_contextベースの独立実装
  - aiohttp_lifespan(name, **kwargs) / CurrentAiohttpClient(name)
  - httpx_lifespan(name, **kwargs) / CurrentHttpxClient(name)
```

`core_toolkit/lifespan.py`は`LifespanResource`/`create_lifespan`/`app_state_dependency`という汎用基盤のみを残し、`aiohttp`/`httpx`のimportを取り除く（未宣言依存バグの解消そのもの）。

### core-toolkit: `aiohttp_lifespan.py`

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

    Args:
        name: このセッションを識別する名前（例: ``"backend"``）。
            ``get_aiohttp_client`` で同じ名前を指定して取得する。
        session_kwargs: ``aiohttp.ClientSession`` にそのまま渡す追加引数
            （``connector=``, ``timeout=``, ``base_url=`` 等）。
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

`httpx_lifespan.py`は同型（`HttpxLifespanResource`/`get_httpx_client`、`app.state.httpx_clients`、`httpx.AsyncClient(**client_kwargs)`）。

### fastapi-toolkit: 薄いラッパー2本

```python
# fastapi_toolkit/aiohttp_lifespan.py
from core_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client

__all__ = ["AioHttpLifespanResource", "get_aiohttp_client"]
```

`httpx_lifespan.py`も同型。名前ごとの型エイリアスはDB/Redisと同じくアプリ側が定義する。

### fastmcp-toolkit: 独立実装2本

```python
# fastmcp_toolkit/aiohttp_lifespan.py
def _lifespan_key(name: str) -> str:
    return f"aiohttp_client:{name}"

def aiohttp_lifespan(name: str, **session_kwargs: Any) -> Lifespan:
    @lifespan
    async def _aiohttp_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        session = aiohttp.ClientSession(**session_kwargs)
        try:
            yield {_lifespan_key(name): session}
        finally:
            await session.close()
    return _aiohttp_lifespan

def CurrentAiohttpClient(name: str) -> aiohttp.ClientSession:  # noqa: N802
    return cast(aiohttp.ClientSession, Depends(_get_aiohttp_client(name)))
```

`httpx_lifespan.py`も同型（`CurrentHttpxClient(name)`）。

## extras構成

```toml
# core-toolkit/pyproject.toml
[project.optional-dependencies]
aiohttp = ["aiohttp>=3.14.1"]
httpx = ["httpx>=0.28"]
```

`dev` extraからは`aiohttp`/`httpx`の直接記載を外し、`uv sync --all-extras`（CLAUDE.md記載の開発コマンド）経由で全extraが入る現行の運用に委ねる（DBの`sqlite`/`postgres`/`mysql`と同じ扱い）。fastapi-toolkit/fastmcp-toolkitは`core-toolkit[aiohttp]`/`core-toolkit[httpx]`を参照する形で自分のextraを定義する。

## bffサンプルの移行方針

`app.py`の`AioHttpLifespanResource()`（引数なし）を`AioHttpLifespanResource("backend", **旧デフォルト相当のkwargs)`に変更し、`backend_a`/`backend_b`/`backend_b_c`の3エンドポイントは`req.app.state.http_client`直接アクセスをやめ、`Annotated[aiohttp.ClientSession, Depends(get_aiohttp_client("backend"))]`型エイリアス経由に統一する。バックエンドA/Bを1つの名前付きクライアントで済ませるか、別名で分けるかは実装計画時に確認する。

## テスト方針

- core-toolkit: `AioHttpLifespanResource`/`get_aiohttp_client`、`HttpxLifespanResource`/`get_httpx_client`それぞれに、登録→取得→close確認・名前未登録時のRuntimeError・複数named clientの独立性を検証するテストを新設する（DB/Redisと同じ構成）。実サーバーへの接続は不要（`aiohttp.ClientSession`/`httpx.AsyncClient`はセッション生成自体はネットワークI/Oを伴わない）
- fastapi-toolkit: 薄いラッパーのDepends解決を検証する統合テストを新設（DB/Redisと同じ粒度）
- fastmcp-toolkit: RPC境界のパラメータ名match方式・直接呼び出しユニットテストをDB/Redisと同じパターンで新設

## 未決事項・将来検討（本設計のスコープ外）

- bffのbackend_a/backend_bを別クライアントに分割するかどうか（writing-plans時に確認）
- fastmcp-toolkit側でHTTPクライアントを実際に使うサンプル（DB/Redisのcacheサンプルに相当するもの）を作るかどうか
- `aiohttp`/`httpx`以外のHTTPクライアントライブラリへの対応
