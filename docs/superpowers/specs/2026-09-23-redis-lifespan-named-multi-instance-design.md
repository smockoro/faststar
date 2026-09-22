# Redis接続lifespan部品 再設計（名前付き複数インスタンス化）

- 日付: 2026-09-23
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`
- ステータス: ドラフト（ユーザーレビュー待ち）

## 背景・目的

以前のセッションでRedis lifespan統合（`RedisLifespanResource`/`RedisClient`/
`redis_lifespan()`・`CurrentRedisClient()`）を3パッケージへ実装する作業が
進行していたが、未コミットのままDB接続部品の実装とファイル競合し、
`redis-wip-stash`ブランチ（コミット`e7ace3d`）へ一時退避されて放置された。
`redis-wip-stash`はDB接続部品より前の古い`main`から分岐しており、そのまま
では現在の`main`（`b9aa4e8`）に適用できない。

加えてDB接続部品の実装過程で、以下の技術的知見が得られている
（`docs/superpowers/specs/2026-09-22-fastmcp-db-connection-design.md`、
コミット`a9b2fb9`）。

- FastMCPの`Client.call_tool`はRPC境界を越える際にサーバー側の詳細な例外
  メッセージを保持しない。クライアントに届くのは`Depends`解決対象の
  **パラメータ名**を含む`"Failed to resolve dependency '<param>' for <fn>"`
  のみ
- そのため「未登録時にエラーになること」を検証するテストは、パラメータ名で
  `match`する必要がある。旧Redis WIPの該当テストは`redis`というパラメータ名
  とたまたま一致していただけで、この仕組みを踏まえた実装ではなかった
- エラーメッセージの中身（登録名のヒント等）を検証したい場合は、RPC越しでは
  なく依存解決関数を直接呼ぶユニットテストを別途書く必要がある

これらを踏まえ、旧WIPをベースに移植するのではなく、DB接続部品と同じ設計
原則・同じ落とし穴対策を最初から組み込んで**新規に書き直す**。

## スコープ

- Redisクライアント（接続プール）のlifespan管理
- 名前付き複数Redisクライアントの同時利用（DBと同型のAPIに統一）
- core-toolkit / fastapi-toolkit / fastmcp-toolkitの3層への配置
- 上記3パッケージのテスト整備（DB接続部品と同水準のカバレッジ）
- fastmcp-toolkitのcacheサンプル（`examples/cache_*.py`）の再作成と、
  fastapi-toolkit側（`examples/bff/src/bff/cache/`）との整合

## 非スコープ（意図的に含めない）

- 呼び出し単位のトランザクション境界（DBの`get_db_connection`に相当する
  もの）— Redisクライアントはコネクションプール自体が並行安全であり、
  DBのような「1回のDepends解決＝1トランザクション」という境界を必要と
  しない。`MULTI`/`EXEC`やパイプラインが必要な場合はアプリ側が取得した
  `Redis`クライアントに対して直接呼べばよく、toolkit側に追加APIは設けない
- Repository/Usecaseパターンの提供 — toolkit本体には含めず、`examples/`側の
  リファレンス実装にとどめる
- Redis Cluster / Sentinel構成への専用対応 — `redis.asyncio.Redis.from_url`が
  素朴に対応できる範囲を超える構成は今回のスコープ外（必要になった時点で
  別途検討）
- `redis-wip-stash`ブランチ自体のmerge/cherry-pick — 参考にするのみで、
  ブランチは今回の作業が終わったら破棄してよい

## 意思決定サマリー

| 論点 | 決定 | 理由 |
|---|---|---|
| クライアント管理方式 | 名前付きで複数登録・取得できるAPI（`RedisLifespanResource(name=...)`/`get_redis_client(name)`/`CurrentRedisClient(name)`） | DBと同じ課題（例: キャッシュ用とセッション用でRedisを分けたい）が将来起こり得るため、db_lifespanと対称なAPIに揃える。旧WIPの「単一グローバルクライアント」は破棄 |
| 旧`redis-wip-stash`の扱い | 参考資料として読むのみ。コードは新規に書く | 古いmainから分岐しておりそのまま適用できない上、RPC例外テストの実装ミスなど、db_lifespan開発で得た知見を反映していない箇所がある |
| トランザクション境界相当のAPI | 設けない（`get_redis_client`のみ） | Redisクライアントはリクエスト単位でチェックアウトする必要がなく、DBの`get_db_connection`に相当する概念がそもそも存在しない |
| fastmcp-toolkitのcacheサンプル構造 | フラットファイル構成（`cache_domain.py`等）を維持し、パッケージ化はしない | fastmcp-toolkitの既存`examples/`は`simple_server.py`的な単一スクリプト構成が慣習であり、bffのような独立uvワークスペースメンバーにはなっていない。無理に合わせる理由がない |
| テストのRPC例外検証方法 | パラメータ名でのマッチであることをコメントで明記し、さらに依存解決関数を直接呼ぶユニットテストを別途追加 | db_lifespanで発覚した「たまたま一致していただけ」の再発防止 |

## アーキテクチャ

### レイヤー構造（db_lifespanと同型）

```
core-toolkit/src/core_toolkit/redis_lifespan.py
  - RedisLifespanResource（LifespanResourceのサブクラス、名前付き複数登録対応）
  - get_redis_client(name)   … provider関数生成
  - redis.asyncioにのみ依存。FastAPI/FastMCPを一切importしない

fastapi-toolkit/src/fastapi_toolkit/redis_lifespan.py
  - 上記を再エクスポートするだけの薄いラッパー
  - 名前ごとの型エイリアスはtoolkit側では定義せず、アプリ側が名前を
    指定して自分で作る（db_lifespanと同じ運用）

fastmcp-toolkit/src/fastmcp_toolkit/redis_lifespan.py
  - FastMCP独自のlifespan形状（lifespan_contextにdictをyield）向けの
    独立実装
  - uncalled_for.Depends＋CurrentRedisClient(name)という
    「呼ぶとDependsを返す関数」パターン
```

### core-toolkit: `redis_lifespan.py`

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

`AsyncGenerator`のimportは`context`の型注釈でのみ使用（`db_lifespan.py`と
同じ書き方に揃える）。

### fastapi-toolkit: `redis_lifespan.py`

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

### fastmcp-toolkit: `redis_lifespan.py`

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

`db_lifespan.py`の`CurrentDbEngine`/`_get_db_engine`と1対1対応の構造。

## テスト方針

DB接続部品で得た知見（RPC境界での例外情報欠落、`AsyncEngine.dispose`の
クラスレベルmonkeypatch必要性）のうち、Redisに該当するのはRPC境界の話のみ
（Redisクライアントに`dispose`相当の「呼んでも失敗しない」罠は無いため
対象外）。以下を各パッケージに追加する。

### core-toolkit: `tests/test_redis_lifespan.py`（新規、db_lifespanのテストと対で追加）

- 正常系: 起動→クライアント取得→close確認（`fakeredis`使用、`aclose`が
  呼ばれたことを`monkeypatch`でトラッキング）
- 名前未登録時に`RuntimeError`（登録名を含むメッセージ）を送出すること
- 複数named clientsが独立して登録・取得できること

### fastmcp-toolkit: `tests/test_redis_lifespan.py`（書き直し）

`test_db_lifespan.py`と同じ構成に揃える。

1. `Client(app)`経由でツール関数から`CurrentRedisClient(name)`がDepends解決
   されること（`fakeredis`使用）
2. 複数named clientsが独立していること
   （`test_multiple_named_engines_are_independent`相当）
3. lifespan未登録時、RPC越しに例外が送出されること。ただし
   **`match`はDepends解決対象のパラメータ名に対して行う**ことをコメントで
   明記する（例: ツール関数の引数名が`redis`なら`match="redis"`だが、これは
   RuntimeErrorのメッセージ内容ではなくパラメータ名と一致しているという
   ことをコメントで示す）
4. `_get_redis_client`を直接呼ぶユニットテストを追加し、詳細な
   `RuntimeError`メッセージ（登録名のヒント）を検証する
   （`test_get_db_engine_error_message_includes_registration_hint`相当）

### fastapi-toolkit: `tests/test_redis_lifespan.py`（新規）

旧WIPには存在しなかった、本体パッケージ直下の単体テスト。
`test_db_lifespan.py`と同じ粒度で、`Depends(get_redis_client(name))`が
正しく解決されることを確認する（`fastapi.testclient.TestClient`または
直接provider関数を呼ぶユニットテストのいずれか、実装時に既存の
`test_lifespan.py`の書き方に揃える）。

## サンプル（cacheサンプル）の方針

- `fastapi-toolkit/examples/bff/src/bff/cache/`（domain/repository/usecase/
  dependencies/router）を、`redis_lifespan.py`のAPI変更（`get_redis_client`
  が名前必須になった点）に合わせて再作成する
- `fastmcp-toolkit/examples/cache_*.py`（domain/repository/usecase/
  dependencies/server の5ファイル構成）も同様に再作成する。パッケージ化は
  せず、既存の`examples/simple_server.py`と同じフラットスクリプト構成を
  踏襲する
- 旧WIPのfastmcp cacheサンプルにはテストが無かった点を埋め合わせ、fastapi
  側の`examples/bff/tests/cache/`に相当する簡単な動作確認テストを
  fastmcp-toolkit側にも追加する（`Client(app)`経由でread/writeツールを
  呼ぶ統合テスト1本で十分、bffのように結合テスト・ユニットテストを分ける
  必要はない）

## extras構成

```toml
[project.optional-dependencies]
redis = ["redis>=5.0"]
dev = ["fakeredis>=2.20", ...]  # 既存devの内容に追加
```

- core-toolkit/fastapi-toolkit/fastmcp-toolkitの3パッケージすべてに`redis`
  extraを追加する（現状の`main`にはどのパッケージにも存在しない）
- fastapi-toolkitは`core-toolkit[redis]`を`redis`extra経由で引く
  （DB接続部品の`sqlite`/`postgres`/`mysql`extraと同じ依存の通し方）
- バージョン下限は実装時に`uv add`で解決し直す

## 未決事項・将来検討（本設計のスコープ外）

- Redis Cluster/Sentinel構成への対応
- パイプライン/`MULTI`・`EXEC`をtoolkit側でラップするAPIの要否
  （現時点では不要と判断。必要になった時点で別途検討）
- `core_toolkit/health.py`とのヘルスチェック統合
- `redis-wip-stash`ブランチの削除タイミング（今回の実装が一通り完了し、
  参照する必要が無くなった時点でユーザーの判断のもと削除する）
