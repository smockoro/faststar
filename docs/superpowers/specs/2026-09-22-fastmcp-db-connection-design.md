# FastMCP向けDB接続部品 設計

- 日付: 2026-09-22
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`
- ステータス: ドラフト（ユーザーレビュー待ち）

## 背景・目的

faststarにDB接続部品を設ける。まずSQLiteを利用することを想定しつつ、将来的に
Postgres/MySQL（さらにDuckDB等）へ拡張できることを要件とする。

既存の `RedisLifespanResource`（core-toolkit）/ `RedisClient`（fastapi-toolkit）/
`redis_lifespan()`・`CurrentRedisClient()`（fastmcp-toolkit）と同じ「起動時に
接続を確立してapp全体で共有し、終了時にclose」というlifespanリソース管理の
延長線上に位置づける。ただしDB固有の論点（複数バックエンド対応・トランザク
ション境界・複数エンジン同時利用）がありRedisの実装をそのまま流用できない
ため、本ドキュメントで設計を確定する。

## スコープ

- DBエンジン（コネクションプール）のlifespan管理
- 呼び出し単位（Depends解決1回）でのトランザクション境界（begin/commit/
  rollback/close）の提供
- 名前付き複数DBエンジンの同時利用
- core-toolkit / fastapi-toolkit / fastmcp-toolkitの3層への配置

## 非スコープ（意図的に含めない）

- Repository/Usecase/UnitOfWorkパターンの提供 — toolkit本体には含めず、
  `examples/`配下のリファレンス実装として示す（Redisの`cache/`例と同じ位置
  づけ）
- 入れ子トランザクション（SAVEPOINT）・独立トランザクション（REQUIRES_NEW
  相当）の抽象化 — `AsyncConnection.begin_nested()`や`AsyncEngine.begin()`と
  いうSQLAlchemy標準機能をアプリ側がそのまま使えば足りるため、toolkit側の
  追加APIは設けない
- マイグレーション管理（Alembic等の統合）
- 生ドライバー（SQLAlchemyを介さない）実装 — 学習目的での検討は別ブランチ
  で個別に行う。本設計の対象外

## 意思決定サマリー

| 論点 | 決定 | 理由 |
|---|---|---|
| DB抽象化手段 | SQLAlchemy（Core、ORM機能は使わない） | 接続文字列・コネクションプール・パラメータバインド・例外階層をdialectごとに統一済み。自前実装は同じものの再発明になる |
| 実行モデル | 非同期ドライバのみ（`aiosqlite`/`asyncpg`/`asyncmy`） | 既存toolkitが非同期前提。DuckDB等の同期専用ドライバは非対応とし、必要になった時点で別途検討する（YAGNI） |
| トランザクション境界 | Depends連鎖の起点（`get_db_connection`）が呼び出し単位でbegin/commit/rollback/closeを行う「暗黙的UoW」 | Repository/Usecaseがトランザクション管理をimportしない既存DI原則（Depends連鎖・コンストラクタインジェクション）を維持できる。Spring `@Transactional`のようなデコレータ＋contextvarはRepositoryの暗黙依存を生み既存原則と衝突するため不採用 |
| 複数DBエンジン | 名前付きで複数登録・取得できるAPIを最初から用意 | Redisの単一グローバルクライアント前提では「メインDBはPostgres、分析用は別DB」のような利用ができないため |
| extras構成 | DB種別ごとに`sqlite`/`postgres`/`mysql`を独立させ、各extraに`sqlalchemy[asyncio]`を重複して含める | 「個別に分ける」という要望に沿い、どれか1つのextraだけで単独install可能にする |
| テスト方針 | `sqlite+aiosqlite:///:memory:`で汎用ロジックを検証、Postgres/MySQL実サーバーはCI不要 | `fakeredis`でRedis lifecycleを検証している既存方針と同型 |

## アーキテクチャ

### レイヤー構造（Redisと同型）

```
core-toolkit/src/core_toolkit/db_lifespan.py
  - DbLifespanResource（LifespanResourceのサブクラス）
  - get_db_engine(name)   … 生Engineアクセス用provider生成関数
  - get_db_connection(name) … 呼び出し単位のConnection（トランザクション境界付き）provider生成関数
  - SQLAlchemyにのみ依存。FastAPI/FastMCPを一切importしない

fastapi-toolkit/src/fastapi_toolkit/db_lifespan.py
  - 上記を再エクスポートするだけの薄いラッパー
  - 名前ごとの型エイリアス（`Annotated[..., Depends(get_db_engine("main"))]`）は
    toolkit側では定義せず、アプリ側が名前を指定して自分で作る

fastmcp-toolkit/src/fastmcp_toolkit/db_lifespan.py
  - FastMCP独自のlifespan形状（`lifespan_context`にdictをyield）向けの独立実装
  - `uncalled_for.Depends`＋`CurrentDbEngine(name)`/`CurrentDbConnection(name)`
    という「呼ぶとDependsを返す関数」パターン（`CurrentRedisClient()`踏襲）
```

`app_state_dependency`（Redis/HTTPが使う、単一属性を`getattr`するだけの関数）
は変更しない。DB用の`get_db_engine`/`get_db_connection`は名前解決とyieldベース
の後処理が必要なため、別関数として追加する。

### core-toolkit: `db_lifespan.py`

```python
class DbLifespanResource(LifespanResource):
    """名前付きでAsyncEngineを登録するリソース。同じappに複数登録できる。"""

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
    """

    def get_engine(request: Request) -> AsyncEngine:
        engines: dict[str, AsyncEngine] = getattr(request.app.state, "db_engines", {})
        if name not in engines:
            raise RuntimeError(
                f"db_engines['{name}'] is not set. "
                f"Did you forget to register DbLifespanResource(name='{name}', ...)?"
            )
        return engines[name]

    get_engine.__name__ = f"get_db_engine_{name}"
    return get_engine


def get_db_connection(
    name: str,
) -> Callable[[Request], AsyncGenerator[AsyncConnection, None]]:
    """名前を指定して呼び出し単位のConnectionを取得するprovider関数を生成する。

    Depends解決のライフタイム＝1トランザクションとして扱う。正常終了でcommit、
    例外でrollback、いずれの場合も必ずclose（``engine.begin()``のcontext
    manager挙動に従う）。入れ子トランザクションが必要な場合は、取得した
    ``AsyncConnection``に対してアプリ側が``begin_nested()``を直接呼ぶ
    （本モジュールは追加APIを提供しない）。
    """

    async def get_connection(request: Request) -> AsyncGenerator[AsyncConnection, None]:
        engine = get_db_engine(name)(request)
        async with engine.begin() as conn:
            yield conn

    get_connection.__name__ = f"get_db_connection_{name}"
    return get_connection
```

### fastapi-toolkit: `db_lifespan.py`

```python
from core_toolkit.db_lifespan import DbLifespanResource, get_db_engine, get_db_connection

__all__ = ["DbLifespanResource", "get_db_engine", "get_db_connection"]
```

再エクスポートのみ。名前ごとの型エイリアスはアプリ側で以下のように定義する。

```python
# アプリ側（例）
MainDbConnection = Annotated[AsyncConnection, Depends(get_db_connection("main"))]
AnalyticsDbEngine = Annotated[AsyncEngine, Depends(get_db_engine("analytics"))]
```

### fastmcp-toolkit: `db_lifespan.py`

`redis_lifespan.py`と同じ構造（`lifespan_context`にdictをyieldする独立実装）
とし、複数DBを`|`演算子で合成できるよう`lifespan_context`のキーを名前で
分ける（`f"db_engine:{name}"`）。

```python
def db_lifespan(name: str, url: str, **engine_kwargs: Any) -> Lifespan:
    @lifespan
    async def _db_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        engine = create_async_engine(url, **engine_kwargs)
        try:
            yield {f"db_engine:{name}": engine}
        finally:
            await engine.dispose()

    return _db_lifespan


def CurrentDbEngine(name: str) -> AsyncEngine:  # noqa: N802
    ...  # ctx.lifespan_context[f"db_engine:{name}"] を取得するDependsを返す


def CurrentDbConnection(name: str) -> AsyncConnection:  # noqa: N802
    ...  # CurrentDbEngine(name)からengine.begin()でConnectionを取得しyieldするDependsを返す
```

## トランザクション境界の設計判断（詳細）

Depends連鎖のUsecaseコンストラクタで`Connection`を受け取る場合、トランザク
ションが「始まる」のはUsecaseの構築時ではなく、**Depends連鎖の起点
（`get_db_connection`）が解決される時点**であり、Usecase/Repository構築より
前である。

```
get_db_connection 呼び出し → engine.begin()でトランザクション開始
  → その conn を使って Repository 構築
    → その Repository を使って Usecase 構築
      → Controller層のハンドラが Usecase のメソッドを呼ぶ
      → ハンドラが返る/例外を投げる
  → get_db_connection の yield 以降が再開
    → 正常終了: commit / 例外: rollback → いずれも最後に close
```

Usecase/Repositoryのコンストラクタは受け取った`Connection`を保持するだけの
受動的な処理であり、`engine.begin()`や`commit()`を一切呼ばない。トランザク
ション境界の知識は`get_db_connection`という1つのprovider関数に閉じている。

### 入れ子トランザクションが必要な場合

`AsyncConnection.begin_nested()`（SAVEPOINT）をアプリ側のUsecase/Repository
が直接呼ぶ。toolkit側はConnectionを渡すところまでが責務であり、追加のAPIは
提供しない。

```python
async def execute(self, order: Order) -> None:
    for item in order.items:
        try:
            async with self._conn.begin_nested():  # SAVEPOINT
                await self._inventory_repo.reserve(item.id, item.qty)
        except OutOfStockError:
            await self._order_repo.mark_item_backordered(item.id)
    await self._order_repo.create(order)
```

Usecaseの「SQLAlchemyを直接importしない」という既存原則を厳密に保ちたい場合
は、アプリ側で`UnitOfWork`ABCを定義し`nested()`メソッドでラップする選択肢が
あるが、これもtoolkitではなくアプリ側（examples）の設計判断とする。

独立トランザクション（REQUIRES_NEW相当、外側の失敗に影響されない別トランザ
クション）が必要な場合は、注入された`conn`を使い回さず`get_db_engine(name)`
で取得した`AsyncEngine`から新たに`engine.begin()`すればよい。

## extras構成

```toml
[project.optional-dependencies]
redis = ["redis>=5.0"]
sqlite = ["sqlalchemy[asyncio]>=2.0", "aiosqlite>=0.20"]
postgres = ["sqlalchemy[asyncio]>=2.0", "asyncpg>=0.29"]
mysql = ["sqlalchemy[asyncio]>=2.0", "asyncmy>=0.2"]
dev = ["fakeredis>=2.20", "pytest>=8.0", "pytest-asyncio>=0.24", "ruff>=0.8"]
```

- `sqlite`/`postgres`/`mysql`はそれぞれ単独installで完結する（`sqlalchemy[asyncio]`
  の重複記述はuv/pipのextra解決でマージされる）
- MySQLは`asyncmy`を既定ドライバとする。`aiomysql`に変更したい場合はURL
  スキーム（`mysql+aiomysql://`）とextraの中身を変えるだけで、core-toolkit側
  のコードは変更不要
- `sqlalchemy`自体は`db_lifespan.py`が無条件importするが、`db_lifespan.py`は
  `redis_lifespan.py`と同じ「オプトインサブモジュール」（使う側がextra込みで
  明示的にimportする）という位置づけのため、base dependenciesには含めない
- 具体的なバージョン下限（`aiosqlite>=0.20`等）は実装時に`uv add`で解決し
  直し、ここでの数値は目安とする
- `dev`extraに`sqlite`は含めない。`uv sync --all-extras`（CLAUDE.md記載の
  開発コマンド）で全extraが入るため、テストに必要な`aiosqlite`は自然に揃う

## テスト方針

- `DbLifespanResource`/`get_db_engine`/`get_db_connection`の汎用ロジック
  （commit/rollback/close・名前未登録時のエラーメッセージ・複数エンジン
  registrationの独立性）は`sqlite+aiosqlite:///:memory:`で検証する
- Postgres/MySQL固有のdialect差異はSQLAlchemy側の責務であり、faststar側の
  テスト対象外とする。CIに実DBサーバーは不要
- fastapi-toolkit/fastmcp-toolkitの薄いラッパー層は、Redisの既存テスト
  （`test_lifespan.py`/`test_redis_lifespan.py`）と同じ粒度で、Depends解決が
  正しくprovider関数へ委譲されることを確認する

## 未決事項・将来検討（本設計のスコープ外）

- DuckDB等の同期専用ドライバへの対応（非同期一本の現行方針を維持する限り
  未対応。必要になった時点で別途設計する）
- Repository/Usecase/UnitOfWorkパターンのリファレンス実装（`examples/`側で
  別途作成する。本設計はcore-toolkit/fastapi-toolkit/fastmcp-toolkitのAPI
  のみを対象とする）
- 生ドライバー実装の学習用検証（別ブランチで個別に実施）
- `core_toolkit/health.py`とのヘルスチェック統合（既存のhealth check機構に
  DB接続の死活監視をどう組み込むかは未検討）
