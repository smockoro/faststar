# エラー処理系部品 設計検討（BusinessProblem/SystemProblem分類 + API/MCP出口分離）

- 日付: 2026-09-28
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`
- ステータス: ドラフト（設計方針の検討記録。実装はまだ着手しない。[Issue #2](https://github.com/smockoro/faststar/issues/2)の後継検討）

## 背景・目的

[Issue #2](https://github.com/smockoro/faststar/issues/2)で開始した、エラー処理系共通部品の設計検討の続き。Issue本文で立てた「検討中の設計方針」を、実際の実装イメージ・既存OSS実装の調査・MCPプロトコルの制約に照らして深掘りし、決定事項と未決事項を切り分けたもの。

Issue #2本文の要点（前提として踏襲する）:

- 例外クラス階層は core-toolkit に置く（プロトコル非依存）
- 例外→レスポンス変換は toolkit ごとに分離する
- 実装は「標準の`exception_handlers`（主経路）」＋「一番外側の安全網ミドルウェア」の二層構成にする（※本ドキュメントでfastapi-toolkit固有の話だと判明。後述）

## スコープ

- 例外クラス階層の深さと属性設計（core-toolkit）
- `error_code` からプロトコル固有コードへの変換方式（fastapi-toolkit / fastmcp-toolkit）
- 上記変換テーブルのライフサイクルと配置（lifespan resource化の要否・置き場所）
- fastapi-toolkit側のエラーレスポンスのペイロード構造
- fastmcp-toolkit側のエラーレスポンス（`CallToolResult`）の組み立て方

## 非スコープ（意図的に今回は決めない）

- `error_code` の命名規則（ドット区切り階層にするか等）
- `msal_errors.py`（`MsalTokenError`系）の統合方法（`SystemProblem`のサブクラスにするか、独立のまま残すか）
- ログ記録（structlog / OpenTelemetry span）を`exception_handler`側と安全網ミドルウェア側のどちらの責務にするか、二重記録の防止
- 設定値管理部品そのものの設計（別Issueで検討予定。本ドキュメントでは「差し替えポイントを用意する」ところまで）
- fastmcp-toolkit側で`structuredContent`を使う設計（将来、機械的なエラーハンドリングの需要が出た場合に再検討）

## 参考実装調査: MarioKartCentral

Issue #2が参照した [MarioKartCentral/MarioKartCentral](https://github.com/MarioKartCentral/MarioKartCentral) の `src/backend` を実際に読んだ結果、**サブクラスをまったく作らない**設計だった。

```python
# common/data/models/common.py
@dataclass
class Problem(Exception):
    title: str
    detail: str | None = None
    status: int = 500
    data: dict[str, Any] | None = None
```

呼び出し側は`raise Problem("User not found", status=404)`のように`Problem`を直接インスタンス化する。`BusinessProblem`/`SystemProblem`のような型分類も、`NotFoundError`のような個別サブクラスも存在せず、`status >= 500`かどうかでログレベルを分けている（`api/utils/middleware.py`の`handle_problem`）。

「クラスを増やし続ける問題」への解の一つとして参考にしたが、`status: int`というHTTP語彙をcore相当の型が直接持つ設計であり、MCP出口を持たないfaststarにはそのまま持ち込めない（後述のとおり、faststarでは`error_code`という抽象値からtoolkitごとに解決する形にする）。ただし「二層構成（`exception_handlers` + `ProblemExceptionMiddleware`）」自体は実装を確認でき、ユーザー定義ミドルウェア（`RateLimitByIPMiddleware`）が送出した`Problem`も安全網側で正しく拾えていた。

## 意思決定サマリー

| 論点 | 決定 | 理由 |
|---|---|---|
| 例外クラス階層の深さ | `ApplicationProblem` → `BusinessProblem`（既定4xx） / `SystemProblem`（既定5xx）の2段のみ。個別サブクラス（`NotFoundError`等）は作らない | サブクラスを作り続けるのは非現実的。`error_code`はtoolkit組み込み分＋利用者アプリ側の分でどんどん増えるが、型は増やしたくない |
| エラー種別の識別 | `error_code: str` をインスタンス属性として持つ。型ではなく値で識別する | 増え続けるエラー種別を型で表現すると破綻する。MKC調査で「型を増やさない」設計が現実解だと確認できた |
| HTTPステータスの解決 | fastapi-toolkitに `ErrorCodeRegistry`（`error_code -> http_status`）を置く。未登録の`error_code`は例外の型からデフォルト（Business→400 / System→500）にフォールバック | HTTPは`error_code`ごとの多段階な分類が必要なプロトコルなので、テーブルを引く意味がある |
| JSON-RPCコードの解決 | **持たない**。fastmcp-toolkit側にはレジストリを置かない | MCPツール呼び出しの失敗は`CallToolResult.isError: bool`という2値でしか表現されない。JSON-RPCの`error`（-32xxx）はメソッド不明等ごく一部のプロトコルレベルのケース専用で、アプリ例外はそこに乗らない。変換先の多様性が実質ゼロなのでテーブルは過剰設計 |
| fastmcp-toolkit側のレスポンス | `CallToolResult(isError=True, content=[TextContent(text=f"[{error_code}] {message}")])` を自前で組み立てる | 標準の`_make_error_result`相当は`content`のみを使う実装。`structuredContent`は`outputSchema`との契約と衝突しうるため今回は使わない |
| `ErrorCodeRegistry`のデータソース | Registryの型・解決ロジックと、データの構築元を分離する。今はコード内リテラルの`dict`で組み立てる | 「設定値管理部品」（別Issue予定）がまだ存在しないため。将来、設定ファイルをパースして`dict`を作るローダー関数を1つ足すだけで差し替えられるようにしておく |
| レジストリのライフサイクル | シングルトンのlifespan resource。DB/Redis/ObjectStorageのような名前付き複数インスタンスにはしない | 1アプリ内で`error_code`名前空間を複数持ちたい実需がない |
| レジストリの配置場所 | fastapi-toolkit: `app.state` / fastmcp-toolkit: `lifespan_context` | `exception_handlers`とASGIミドルウェアは、どちらもFastAPIの`Depends`注入の対象外。Dependsで渡せないため、両者から参照できる共有領域として使う（DB接続lifespanが主にDependsでルートハンドラに渡すために使われているのとは、使われ方の目的が異なる） |
| fastapi-toolkitの実装構成 | Issue本文どおり二層（`exception_handlers` + 一番外側の安全網ASGIミドルウェア） | StarletteのExceptionMiddlewareがルーター内で解決するため、ルートハンドラ由来の例外は`exception_handlers`が処理し、安全網ミドルウェアには伝播しない（二重処理にはならない）。認証・レート制限等のユーザー定義ミドルウェア由来の例外だけ安全網が拾う |
| fastmcp-toolkitの実装構成 | 一番外側に登録するエラー変換用`Middleware`1つのみ。二層構成は不要 | `Middleware.on_message`は`call_next`を挟むオニオン構造で、外側1つの`try/except`で内側の全Middleware・ツール実行の例外を拾える。fastapiのような「Dependsが届く/届かない」の分断が存在しない |

## アーキテクチャ

### 例外クラス階層（core-toolkit）

```python
# core_toolkit/errors.py
class ApplicationProblem(Exception):
    """アプリケーション全体の例外基底。プロトコル非依存。"""
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)

class BusinessProblem(ApplicationProblem):
    """想定内・クライアント起因のエラー（HTTP: 既定400系）。"""

class SystemProblem(ApplicationProblem):
    """想定外・インフラ起因のエラー（HTTP: 既定500系）。"""
```

`msal_errors.py`（`MsalTokenError`系）や`object_storage/base.py`（`ObjectStorageError`系）など、既存の機能別例外階層との統合方針は非スコープ（未決事項参照）。

### fastapi-toolkit: RFC7807 + `ErrorCodeRegistry`

レスポンスペイロードはRFC 7807 (Problem Details for HTTP APIs) をそのまま踏襲する。

```python
# core_toolkit/error_mapping.py
@dataclass
class ErrorCodeMapping:
    http_status: int | None = None
    jsonrpc_code: int | None = None  # fastmcp-toolkit側では実質未使用（下記参照）

class ErrorCodeRegistry:
    def __init__(self, mappings: dict[str, ErrorCodeMapping] | None = None) -> None:
        self._mappings = dict(mappings or {})

    def resolve_http_status(self, exc: ApplicationProblem) -> int:
        mapping = self._mappings.get(exc.error_code)
        if mapping and mapping.http_status is not None:
            return mapping.http_status
        return 500 if isinstance(exc, SystemProblem) else 400
```

```python
# fastapi_toolkit側のexception_handler
async def handle_application_error(request: Request, exc: ApplicationProblem) -> JSONResponse:
    resource: ErrorMappingLifespanResource = request.app.state.error_mapping
    status = resource.registry.resolve_http_status(exc)
    return JSONResponse(
        status_code=status,
        content={
            "type": "about:blank",
            "title": exc.error_code,
            "status": status,
            "detail": exc.message,
        },
        media_type="application/problem+json",
    )
```

安全網ミドルウェア（`ErrorHandlingMiddleware`）は、上記と同じ変換ロジックを`scope["app"].state.error_mapping`経由で呼び出す。ロジックの重複を避けるため、変換関数自体は`exception_handler`とミドルウェアの共通ヘルパーとして1箇所に実装する。

### fastmcp-toolkit: 型ベースの変換（レジストリ不要）

```python
# fastmcp_toolkit/middleware/error_handling.py
class ErrorHandlingMiddleware(Middleware):
    async def on_message(self, context, call_next):
        try:
            return await call_next(context)
        except ApplicationProblem as exc:
            log = self.logger.warning if isinstance(exc, BusinessProblem) else self.logger.error
            log(f"{exc.error_code}: {exc.message}", exc_type=type(exc).__name__)
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=f"[{exc.error_code}] {exc.message}")],
            )
```

`error_code`ごとのマッピングテーブルは持たない。`isinstance`によるログレベル分岐と、例外インスタンス自身が持つ`error_code`/`message`をテキスト化するだけで完結する。

### レジストリのライフサイクル・配置

```python
# core_toolkit/error_mapping_lifespan.py
class ErrorMappingLifespanResource:
    def __init__(self, registry: ErrorCodeRegistry) -> None:
        self.registry = registry

def open_error_mapping(mappings: dict[str, ErrorCodeMapping] | None = None) -> ErrorMappingLifespanResource:
    return ErrorMappingLifespanResource(ErrorCodeRegistry(mappings))
```

シングルトンのlifespan resourceとして、fastapi-toolkitは`app.state.error_mapping`、fastmcp-toolkitは`lifespan_context["error_mapping"]`に積む。`exception_handlers`もASGIミドルウェアも`Depends`注入の対象外であるため、Dependsではなくこの共有領域を経由するのが唯一の配布手段になる。

将来「設定値管理部品」ができた場合は、`open_error_mapping(mappings=load_from_config(...))`のように、`mappings`の構築元だけを差し替える。`ErrorCodeRegistry`自体の変更は不要。

## Issue #2本文の訂正が必要な点

Issue #2本文にある「fastmcp-toolkit は JSON-RPC エラー形式へ」という記述は、MCP/FastMCPの実装（`mcp/server/lowlevel/server.py`の`call_tool`ハンドラ）を確認した結果、不正確だと判明した。ツール呼び出しの例外は、JSON-RPCの`error`オブジェクトではなく、**正常なレスポンス(`result`)の中の`CallToolResult(isError=True)`**に変換される。真のJSON-RPC `error`はメソッド不明等ごく一部のプロトコルレベルのケース専用で、アプリケーション例外はそこに乗らない。Issue #2側もこの記述を訂正する。

同様に、方針4「実装は二層構成」もfastapi-toolkit固有の事情に基づくものであり、fastmcp-toolkit側はMiddlewareのオニオン構造により単層で足りる。

## 未決事項（別途検討）

- `error_code` の命名規則（例: `domain.reason` のようなドット区切り階層にするか等）
- `msal_errors.py` の `MsalTokenError` 系を `SystemProblem` のサブクラスとして統合するか、独立のまま残すか
- ログ記録（structlog / OpenTelemetry span）を `exception_handler` 側と安全網ミドルウェア側のどちらの責務にするか、二重記録をどう防ぐか
- `ErrorCodeRegistry` の登録漏れ（未登録`error_code`がデフォルト400/500に落ちる）を起動時やテストでどう検知するか
- fastmcp-toolkit側で将来 `structuredContent` を使う設計に発展させる必要が出た場合の再検討
- 「設定値管理部品」本体の設計（別Issueで検討予定。本ドキュメントは差し替えポイントの用意まで）
