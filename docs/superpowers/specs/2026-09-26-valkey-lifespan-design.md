# Valkey接続lifespan部品 新規設計（名前付き複数インスタンス化）

- 日付: 2026-09-26
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`
- ステータス: ドラフト（ユーザーレビュー待ち）

## 背景・目的

既存の`redis_lifespan.py`（`docs/superpowers/specs/2026-09-23-redis-lifespan-named-multi-instance-design.md`、DBと同型の「名前付き複数インスタンス」設計）と対になる、Valkey接続用のlifespan部品を新規に追加する。

Valkeyの公式クライアントとしては`valkey-glide`（Valkeyプロジェクトが今後の主軸として推している公式マルチ言語クライアント、Rustコア）と`valkey-py`（redis-pyからforkされたAPI互換クライアント）の2choicesがあるが、今回は`valkey-glide`を採用する。

`valkey-glide`はredis-pyの`Redis.from_url(url, **kwargs)`のようなURL一本化ファクトリを持たず、`GlideClientConfiguration`（`addresses: list[NodeAddress]`、`use_tls`、`credentials`、`reconnect_strategy`等を持つ構造化オブジェクト）を介してクライアントを生成する設計になっている。これをそのままtoolkitのAPIとして露出すると、アプリ側のlifespan登録コードが`glide`パッケージの型を直接importする必要が生じ、`redis_lifespan.py`/`db_lifespan.py`が守ってきた「クライアントライブラリの型はcore-toolkit内に閉じ込め、利用側はプリミティブ値だけで基本利用が完結する」という設計原則から外れる。

そのため、`ValkeyLifespanResource`は`host: str, port: int, **config_kwargs`を受け取り、内部で`NodeAddress`/`GlideClientConfiguration`を組み立てる薄いラッパーとする。高度な設定（TLS認証情報、再接続戦略等）を使う場合のみ、利用側が該当のGLIDEオブジェクトを組み立てて`**config_kwargs`経由で渡す（Redis実装の`**client_kwargs`が持つ「稀なケースでは相手先ライブラリの型が漏れる」余地と同格であり、基本利用パスは汚染しない）。

## スコープ

- Valkeyクライアント（`valkey-glide`の`GlideClient`、単一ノード接続）のlifespan管理
- 名前付き複数Valkeyクライアントの同時利用（Redis/DBと同型のAPIに統一）
- core-toolkit / fastapi-toolkit / fastmcp-toolkitの3層への配置
- 上記3パッケージのテスト整備（Redis接続部品と同水準のカバレッジ）

## 非スコープ（意図的に含めない）

- **複数アドレス（プライマリ＋レプリカ）・クラスタ構成（`GlideClusterClient`）** — `host, port`の単一`NodeAddress`のみ対応する。Redis実装がCluster/Sentinelを非スコープとした前例に揃える。必要になった時点で別途検討する
- **Batch/Transaction相当のAPIラップ** — Redis実装がMULTI/EXECを非スコープとした理由（コネクションプール自体が並行安全であり、DBのような「1回のDepends解決＝1トランザクション」という境界を必要としない）と同じ。アプリ側が取得した`GlideClient`に対して直接呼べばよい
- **cache利用サンプル（`fastapi-toolkit/examples/bff/`・`fastmcp-toolkit/examples/`）の新規作成** — Redis実装は既存exampleのAPI変更対応だったが、valkeyは新規追加のため今回は含めない。lifespan部品自体の動作はテストで担保し、サンプルは別途要望があれば着手する
- `valkey-py`（redis-pyフォーク）の並行サポート — 今回は`valkey-glide`のみ

## 意思決定サマリー

| 論点 | 決定 | 理由 |
|---|---|---|
| 公式クライアントの選定 | `valkey-glide` | Valkeyプロジェクトが今後の主軸として推す公式マルチ言語クライアント |
| クライアント管理方式 | 名前付きで複数登録・取得できるAPI（`ValkeyLifespanResource(name=...)`/`get_valkey_client(name)`/`CurrentValkeyClient(name)`） | Redis/DBと同じ課題（用途別に複数インスタンスを分けたい）に対称的なAPIを揃える |
| 初期化引数の形 | `host: str, port: int = 6379, **config_kwargs`（`GlideClientConfiguration`をそのまま受け取らない） | GLIDEの型を利用側コードに漏らさないため。`config_kwargs`は`GlideClientConfiguration`へそのまま素通しする逃げ道として残す |
| 複数ノード（レプリカ・クラスタ）対応 | 非スコープ | 単一ノードのみを対象にAPIを最小に保つ。Redis実装のCluster/Sentinel除外と同じ判断 |
| テスト方式 | `GlideClient.create`をクラスレベルでmonkeypatchしたフェイククライアントを使う | GLIDE公式にはfakeredis相当のフェイクサーバーが存在せず、GLIDE自身のテストも実インスタンス前提のため |

## アーキテクチャ

### レイヤー構造（redis_lifespanと同型）

```
core-toolkit/src/core_toolkit/valkey_lifespan.py
  - ValkeyLifespanResource（LifespanResourceのサブクラス、名前付き複数登録対応）
  - get_valkey_client(name)   … provider関数生成
  - glideにのみ依存。FastAPI/FastMCPを一切importしない

fastapi-toolkit/src/fastapi_toolkit/valkey_lifespan.py
  - 上記を再エクスポートするだけの薄いラッパー

fastmcp-toolkit/src/fastmcp_toolkit/valkey_lifespan.py
  - FastMCP独自のlifespan形状（lifespan_contextにdictをyield）向けの
    独立実装
  - uncalled_for.Depends＋CurrentValkeyClient(name)という
    「呼ぶとDependsを返す関数」パターン
```

### core-toolkit: `valkey_lifespan.py`

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

`redis_lifespan.py`の`RedisLifespanResource`/`get_redis_client`と1対1対応の構造。差分は「`url`一本」ではなく「`host, port, **config_kwargs`から`GlideClientConfiguration`を組み立てる」部分のみ。

### fastapi-toolkit: `valkey_lifespan.py`

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

### fastmcp-toolkit: `valkey_lifespan.py`

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

`redis_lifespan.py`の`CurrentRedisClient`/`_get_redis_client`と1対1対応の構造。

## テスト方針

Redis接続部品との差分点：GLIDE公式には`fakeredis`相当のフェイクサーバーが存在せず、GLIDE自身のテストスイートも実インスタンス前提（`glide_client`フィクスチャで実接続する構成）である。そのため、toolkit側のテストは実接続ではなく`GlideClient.create`をクラスレベルでmonkeypatchする方式にする。

- `monkeypatch.setattr(GlideClient, "create", ...)`でクラス自体にパッチを当てる（DB接続部品で判明した「`AsyncEngine.dispose`のようなclassmethod/staticmethodはインスタンスパッチでは効かない」という落とし穴と同種の対策）
- パッチ先は`close()`を持つフェイククライアント（`unittest.mock.AsyncMock`ベース）を返す非同期関数とする

### core-toolkit: `tests/test_valkey_lifespan.py`（新規、redis_lifespanのテストと対で追加）

- 正常系: 起動→クライアント取得→`close()`が呼ばれたことを確認
- 名前未登録時に`RuntimeError`（登録名を含むメッセージ）を送出すること
- 複数named clientsが独立して登録・取得できること

### fastmcp-toolkit: `tests/test_valkey_lifespan.py`（新規）

`test_redis_lifespan.py`と同じ構成に揃える。

1. `Client(app)`経由でツール関数から`CurrentValkeyClient(name)`がDepends解決されること
2. 複数named clientsが独立していること
3. lifespan未登録時、RPC越しに例外が送出されること。ただし**`match`はDepends解決対象のパラメータ名に対して行う**ことをコメントで明記する
4. `_get_valkey_client`を直接呼ぶユニットテストを追加し、詳細な`RuntimeError`メッセージ（登録名のヒント）を検証する

### fastapi-toolkit: `tests/test_valkey_lifespan.py`（新規）

`test_redis_lifespan.py`と同じ粒度で、`Depends(get_valkey_client(name))`が正しく解決されることを確認する。

## extras構成

```toml
[project.optional-dependencies]
valkey = ["valkey-glide>=1.3"]
```

- core-toolkit/fastapi-toolkit/fastmcp-toolkitの3パッケージすべてに`valkey`extraを追加する
- fastapi-toolkit/fastmcp-toolkitは`core-toolkit[valkey]`を`valkey`extra経由で引く（DB/Redis接続部品と同じ依存の通し方）
- バージョン下限は実装時に`uv add`で解決し直す

## 未決事項・将来検討（本設計のスコープ外）

- 複数アドレス（レプリカ）・クラスタ構成（`GlideClusterClient`）への対応
- `valkey-py`（redis-pyフォーク）の並行サポートの要否
- cache利用サンプル（`fastapi-toolkit/examples/`・`fastmcp-toolkit/examples/`）の新規作成
- `core_toolkit/health.py`とのヘルスチェック統合
