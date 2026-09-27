# エラー処理系部品 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `core-toolkit`に`ApplicationProblem`/`BusinessProblem`/`SystemProblem`の2段例外階層と`ErrorCodeRegistry`を実装し、`fastapi-toolkit`にRFC 7807変換（`exception_handlers` + 安全網ミドルウェア）、`fastmcp-toolkit`に`CallToolResult`変換ミドルウェアを実装する。

**Architecture:** 例外クラス階層とHTTPステータス解決テーブルはcore-toolkitに集約し、プロトコル固有の出口（HTTPレスポンス/CallToolResult）への変換ロジックのみを各toolkitに置く。fastapi-toolkitは`ErrorCodeRegistry`を`app.state`経由のlifespan resourceとして持つが、fastmcp-toolkitはMCPプロトコルの制約（`CallToolResult.isError`が2値のみ）によりレジストリを持たず、`isinstance`分岐のみで完結させる。

**Tech Stack:** Python 3.14 / Starlette / FastAPI / FastMCP / structlog / pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-09-28-error-handling-design.md`

## Global Constraints

- Python 3.14+
- ruffでリント・フォーマット（E, F, I, UPルール）。`uv run ruff check src/ tests/` と `uv run ruff format --check src/ tests/` を各パッケージディレクトリで実行する
- 型ヒントを必ず付ける
- 公開APIのdocstringは日本語・Googleスタイル
- src layout / ビルドバックエンドはhatchling
- structlogベースの構造化ロギング
- 共通ミドルウェアはcore-toolkitに集約し、各toolkitからpath dependencyで参照する
- パッケージ間の依存はcore-toolkitを介してのみ許可（fastapi-toolkitとfastmcp-toolkitは互いに依存しない）
- 例外クラス名は`ApplicationProblem`/`BusinessProblem`/`SystemProblem`とする（設計doc決定時の`ApplicationError`/`BusinessError`/`SystemError`は、`SystemError`が`builtins.SystemError`と同名で読み手が誤認しやすいため、本計画作成時にユーザー判断で改名し、設計docも修正済み）
- core-toolkitのトップレベル`__init__.py`は既存の慣習（`msal_errors`/`object_storage`等の機能別モジュールは個別importさせ、トップレベルにはre-exportしない）に従い、`errors.py`/`error_mapping.py`/`error_mapping_lifespan.py`もトップレベル`__init__.py`には追加しない
- 同様にfastapi-toolkitのトップレベル`__init__.py`にも追加しない（`object_storage_lifespan`等と同じ扱い）
- fastmcp-toolkitの`middleware/__init__.py`は既存モジュールを全てre-exportしているため、これには追加する

## 既知の重要な実装上の落とし穴（各Taskで踏まえる前提）

- **StarletteとFastMCPでミドルウェアの「外側」の意味が逆になる。**
  - Starlette (`fastapi-toolkit`): `Starlette.add_middleware`は`user_middleware.insert(0, ...)`で先頭に挿入し、`build_middleware_stack`は`reversed(middleware)`でラップする。したがって**最後に`add_middleware`したものが一番外側**になる。またルーターの内側に`ExceptionMiddleware`（`exception_handlers`の実体）が常に自動挿入されるため、ルートハンドラ由来の例外は必ず`exception_handlers`が先に処理し、ユーザー定義ミドルウェアより外側には伝播しない。
  - FastMCP (`fastmcp-toolkit`): `FastMCP._run_middleware`は`for mw in reversed(self.middleware)`でチェーンを構築する。したがって**最初に`add_middleware`したものが一番外側**になる。
  - この非対称性を取り違えると、安全網ミドルウェアが「一番外側」ではなく「一番内側」に置かれてしまい、他のユーザー定義ミドルウェアが送出した例外を拾えなくなる。Task 5・Task 7でこの前提を踏まえてdocstring・実装順序を書く。
- fastmcp-toolkitには既に`fastmcp_toolkit/middleware/exception_handler.py`の`ErrorLoggerMiddleware`（全例外をログしてre-raiseするだけ）が存在する。**FastMCPのオニオン構造は「内側が先に例外を見る」**（`_run_middleware`が`reversed(self.middleware)`でラップするため、外側は内側が再raiseした後の例外しか受け取れない）。したがって、新設する`ErrorHandlingMiddleware`を`ErrorLoggerMiddleware`より外側に置くと、`ApplicationProblem`は必ず先に内側の`ErrorLoggerMiddleware`でログ＆re-raiseされてから外側に届いてしまい、二重ログになる。これを避けるため、**`ErrorHandlingMiddleware`は`ErrorLoggerMiddleware`より内側に置く**（`ErrorLoggerMiddleware`を先に`add_middleware`し、`ErrorHandlingMiddleware`を後で`add_middleware`する）。`ErrorHandlingMiddleware`は`ApplicationProblem`のみを捕捉して`CallToolResult`に変換し、re-raiseせずそのまま正常returnする。これにより`ApplicationProblem`は外側の`ErrorLoggerMiddleware`には例外として届かずログされない。`ErrorLoggerMiddleware`は「`ErrorHandlingMiddleware`が変換しなかった（＝`ApplicationProblem`以外の）真に想定外の例外」だけを拾う最終防波堤として機能する。設計doc `docs/superpowers/specs/2026-09-28-error-handling-design.md` の「一番外側に1つだけ置けばよい」という記述は、既存の`ErrorLoggerMiddleware`の存在を考慮していない時点のものであり、本計画ではこの実装上の制約を優先して配置順序を決める。

---

### Task 1: 設計docの命名修正をコミット

設計doc内の`ApplicationError`/`BusinessError`/`SystemError`は、本計画作成時にユーザー判断で`ApplicationProblem`/`BusinessProblem`/`SystemProblem`へ改名した（`SystemError`が`builtins.SystemError`と同名のため）。この作業セッションで`docs/superpowers/specs/2026-09-28-error-handling-design.md`は既に`sed`で一括置換済み（未コミット）。まずこれを独立コミットとして確定させる。

**Files:**
- Modify: `docs/superpowers/specs/2026-09-28-error-handling-design.md`（変更済み、未コミット）

**Interfaces:**
- Consumes: なし
- Produces: 以降の全Taskが参照する確定済み命名（`ApplicationProblem`/`BusinessProblem`/`SystemProblem`）

- [ ] **Step 1: 差分を確認する**

Run: `git -C /home/ubuntu/py-workspace/faststar diff docs/superpowers/specs/2026-09-28-error-handling-design.md`
Expected: `ApplicationError`→`ApplicationProblem`、`BusinessError`→`BusinessProblem`、`SystemError`→`SystemProblem`の置換のみが差分として表示される。

- [ ] **Step 2: コミットする**

```bash
cd /home/ubuntu/py-workspace/faststar
git add docs/superpowers/specs/2026-09-28-error-handling-design.md
git commit -m "docs: rename ApplicationError/BusinessError/SystemError to *Problem

builtins.SystemErrorとの名前衝突を避けるため、設計doc内の例外クラス名を
ApplicationProblem/BusinessProblem/SystemProblemに改名する。"
```

---

### Task 2: core-toolkit — 例外クラス階層（`errors.py`）

**Files:**
- Create: `core-toolkit/src/core_toolkit/errors.py`
- Test: `core-toolkit/tests/test_errors.py`

**Interfaces:**
- Consumes: なし
- Produces: `ApplicationProblem(error_code: str, message: str)`、`BusinessProblem(ApplicationProblem)`、`SystemProblem(ApplicationProblem)`。以降の全Taskがこの3クラスを使う。

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_errors.py`:

```python
"""ApplicationProblem/BusinessProblem/SystemProblemのテスト。"""

from core_toolkit.errors import ApplicationProblem, BusinessProblem, SystemProblem


class TestApplicationProblem:
    def test_holds_error_code_and_message(self):
        exc = ApplicationProblem("test.error", "何かがおかしい")
        assert exc.error_code == "test.error"
        assert exc.message == "何かがおかしい"

    def test_str_returns_message(self):
        exc = ApplicationProblem("test.error", "何かがおかしい")
        assert str(exc) == "何かがおかしい"


class TestBusinessProblem:
    def test_is_application_problem(self):
        exc = BusinessProblem("business.invalid_input", "入力が不正")
        assert isinstance(exc, ApplicationProblem)

    def test_is_not_system_problem(self):
        exc = BusinessProblem("business.invalid_input", "入力が不正")
        assert not isinstance(exc, SystemProblem)

    def test_holds_error_code_and_message(self):
        exc = BusinessProblem("business.invalid_input", "入力が不正")
        assert exc.error_code == "business.invalid_input"
        assert exc.message == "入力が不正"


class TestSystemProblem:
    def test_is_application_problem(self):
        exc = SystemProblem("system.db_unavailable", "DB接続に失敗")
        assert isinstance(exc, ApplicationProblem)

    def test_is_not_business_problem(self):
        exc = SystemProblem("system.db_unavailable", "DB接続に失敗")
        assert not isinstance(exc, BusinessProblem)

    def test_does_not_shadow_builtin_system_error(self):
        """builtins.SystemErrorとは無関係の別クラスであることを固定する回帰テスト。"""
        assert SystemProblem is not SystemError
        assert not issubclass(SystemProblem, SystemError)
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run pytest tests/test_errors.py -v`
Expected: `ModuleNotFoundError: No module named 'core_toolkit.errors'` でFAIL

- [ ] **Step 3: 最小実装を書く**

`core-toolkit/src/core_toolkit/errors.py`:

```python
"""アプリケーション全体の例外階層。プロトコル非依存。

エラー種別は個別サブクラスではなく``error_code``文字列で識別する
（増え続けるエラー種別を型で表現すると破綻するため）。プロトコル固有の
出口（HTTPレスポンス/CallToolResult）への変換は、fastapi-toolkit/
fastmcp-toolkitがそれぞれ担う。詳細は
``docs/superpowers/specs/2026-09-28-error-handling-design.md``を参照。
"""

__all__ = ["ApplicationProblem", "BusinessProblem", "SystemProblem"]


class ApplicationProblem(Exception):
    """アプリケーション全体の例外基底。プロトコル非依存。

    Args:
        error_code: エラー種別を識別する文字列。型ではなく値で種別を表現する。
        message: 人間可読なエラーメッセージ。
    """

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class BusinessProblem(ApplicationProblem):
    """想定内・クライアント起因のエラー（HTTP: 既定400系）。"""


class SystemProblem(ApplicationProblem):
    """想定外・インフラ起因のエラー（HTTP: 既定500系）。"""
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run pytest tests/test_errors.py -v`
Expected: 全件PASS

- [ ] **Step 5: リントを通す**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run ruff check src/core_toolkit/errors.py tests/test_errors.py && uv run ruff format --check src/core_toolkit/errors.py tests/test_errors.py`
Expected: エラーなし

- [ ] **Step 6: コミット**

```bash
cd /home/ubuntu/py-workspace/faststar
git add core-toolkit/src/core_toolkit/errors.py core-toolkit/tests/test_errors.py
git commit -m "feat(core-toolkit): add ApplicationProblem/BusinessProblem/SystemProblem exception hierarchy"
```

---

### Task 3: core-toolkit — `ErrorCodeRegistry`（`error_mapping.py`）

**Files:**
- Create: `core-toolkit/src/core_toolkit/error_mapping.py`
- Test: `core-toolkit/tests/test_error_mapping.py`

**Interfaces:**
- Consumes: `core_toolkit.errors.ApplicationProblem` / `SystemProblem`（Task 2）
- Produces: `ErrorCodeMapping(http_status: int | None = None, jsonrpc_code: int | None = None)`、`ErrorCodeRegistry(mappings: dict[str, ErrorCodeMapping] | None = None)`と`ErrorCodeRegistry.resolve_http_status(exc: ApplicationProblem) -> int`。Task 4・Task 5が使う。

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_error_mapping.py`:

```python
"""ErrorCodeRegistryのテスト。"""

from core_toolkit.error_mapping import ErrorCodeMapping, ErrorCodeRegistry
from core_toolkit.errors import BusinessProblem, SystemProblem


class TestErrorCodeRegistry:
    def test_registered_error_code_returns_mapped_status(self):
        registry = ErrorCodeRegistry(
            {"business.not_found": ErrorCodeMapping(http_status=404)}
        )
        exc = BusinessProblem("business.not_found", "見つからない")
        assert registry.resolve_http_status(exc) == 404

    def test_unregistered_error_code_falls_back_to_400_for_business_problem(self):
        registry = ErrorCodeRegistry()
        exc = BusinessProblem("business.unknown", "未登録")
        assert registry.resolve_http_status(exc) == 400

    def test_unregistered_error_code_falls_back_to_500_for_system_problem(self):
        registry = ErrorCodeRegistry()
        exc = SystemProblem("system.unknown", "未登録")
        assert registry.resolve_http_status(exc) == 500

    def test_mapping_without_http_status_falls_back_to_default(self):
        registry = ErrorCodeRegistry(
            {"system.timeout": ErrorCodeMapping(jsonrpc_code=-32001)}
        )
        exc = SystemProblem("system.timeout", "タイムアウト")
        assert registry.resolve_http_status(exc) == 500

    def test_empty_registry_uses_default_for_business_problem(self):
        registry = ErrorCodeRegistry(None)
        exc = BusinessProblem("business.anything", "何か")
        assert registry.resolve_http_status(exc) == 400
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run pytest tests/test_error_mapping.py -v`
Expected: `ModuleNotFoundError: No module named 'core_toolkit.error_mapping'` でFAIL

- [ ] **Step 3: 最小実装を書く**

`core-toolkit/src/core_toolkit/error_mapping.py`:

```python
"""error_code文字列からプロトコル固有値（HTTPステータス等）への変換テーブル。"""

from dataclasses import dataclass

from core_toolkit.errors import ApplicationProblem, SystemProblem

__all__ = ["ErrorCodeMapping", "ErrorCodeRegistry"]


@dataclass
class ErrorCodeMapping:
    """1つの``error_code``に対応するプロトコル固有値。

    Args:
        http_status: fastapi-toolkitが解決するHTTPステータスコード。
        jsonrpc_code: 予約フィールド。MCPツール呼び出しの失敗は
            ``CallToolResult.isError``という2値でしか表現されないため、
            fastmcp-toolkit側では実質未使用（設計doc参照）。
    """

    http_status: int | None = None
    jsonrpc_code: int | None = None


class ErrorCodeRegistry:
    """``error_code``文字列からHTTPステータスコードを解決するレジストリ。

    Args:
        mappings: ``error_code``をキーとした``ErrorCodeMapping``の辞書。
    """

    def __init__(self, mappings: dict[str, ErrorCodeMapping] | None = None) -> None:
        self._mappings = dict(mappings or {})

    def resolve_http_status(self, exc: ApplicationProblem) -> int:
        """例外に対応するHTTPステータスコードを解決する。

        登録済みの``error_code``があればそのマッピングを優先し、未登録の
        場合は例外の型からデフォルト値（``SystemProblem``→500 / それ以外
        →400）にフォールバックする。

        Args:
            exc: 変換対象の例外。

        Returns:
            解決されたHTTPステータスコード。
        """
        mapping = self._mappings.get(exc.error_code)
        if mapping is not None and mapping.http_status is not None:
            return mapping.http_status
        return 500 if isinstance(exc, SystemProblem) else 400
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run pytest tests/test_error_mapping.py -v`
Expected: 全件PASS

- [ ] **Step 5: リントを通す**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run ruff check src/core_toolkit/error_mapping.py tests/test_error_mapping.py && uv run ruff format --check src/core_toolkit/error_mapping.py tests/test_error_mapping.py`
Expected: エラーなし

- [ ] **Step 6: コミット**

```bash
cd /home/ubuntu/py-workspace/faststar
git add core-toolkit/src/core_toolkit/error_mapping.py core-toolkit/tests/test_error_mapping.py
git commit -m "feat(core-toolkit): add ErrorCodeRegistry for error_code -> http_status resolution"
```

---

### Task 4: core-toolkit — レジストリのlifespan管理（`error_mapping_lifespan.py`）

**Files:**
- Create: `core-toolkit/src/core_toolkit/error_mapping_lifespan.py`
- Test: `core-toolkit/tests/test_error_mapping_lifespan.py`

**Interfaces:**
- Consumes: `core_toolkit.error_mapping.ErrorCodeMapping` / `ErrorCodeRegistry`（Task 3）、`core_toolkit.lifespan.LifespanResource` / `create_lifespan`（既存）
- Produces: `ErrorMappingLifespanResource(mappings: dict[str, ErrorCodeMapping] | None = None)`（`.registry`属性を持つ）、`open_error_mapping(mappings=None) -> ErrorMappingLifespanResource`、`get_error_mapping_resource(app: Starlette) -> ErrorMappingLifespanResource`。Task 5が使う。

シングルトンのlifespan resourceであり、DB/Redis/ObjectStorageのような名前付き複数インスタンスにはしない（1アプリ内で`error_code`名前空間を複数持ちたい実需がないため。設計doc参照）。`exception_handlers`とASGIミドルウェアはどちらもFastAPIの`Depends`注入の対象外であるため、`app.state`を共有領域として使う。`Request`経由ではなく`Starlette`アプリ本体から直接取得する`get_error_mapping_resource`を用意する。

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_error_mapping_lifespan.py`:

```python
"""ErrorMappingLifespanResourceのテスト。"""

import pytest
from starlette.applications import Starlette

from core_toolkit.error_mapping import ErrorCodeMapping
from core_toolkit.error_mapping_lifespan import (
    get_error_mapping_resource,
    open_error_mapping,
)
from core_toolkit.errors import BusinessProblem
from core_toolkit.lifespan import create_lifespan


class TestOpenErrorMapping:
    def test_returns_resource_with_registry(self):
        resource = open_error_mapping({"business.x": ErrorCodeMapping(http_status=422)})
        exc = BusinessProblem("business.x", "検証エラー")
        assert resource.registry.resolve_http_status(exc) == 422

    def test_no_mappings_uses_default_resolution(self):
        resource = open_error_mapping()
        exc = BusinessProblem("business.unknown", "未登録")
        assert resource.registry.resolve_http_status(exc) == 400


class TestErrorMappingLifespanIntegration:
    @pytest.mark.asyncio
    async def test_context_registers_resource_on_app_state(self):
        app = Starlette()
        lifespan = create_lifespan(
            open_error_mapping({"business.x": ErrorCodeMapping(http_status=422)})
        )

        async with lifespan(app):
            resource = get_error_mapping_resource(app)
            exc = BusinessProblem("business.x", "検証エラー")
            assert resource.registry.resolve_http_status(exc) == 422

    def test_get_error_mapping_resource_raises_when_not_registered(self):
        app = Starlette()
        with pytest.raises(RuntimeError, match="error_mapping"):
            get_error_mapping_resource(app)
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run pytest tests/test_error_mapping_lifespan.py -v`
Expected: `ModuleNotFoundError: No module named 'core_toolkit.error_mapping_lifespan'` でFAIL

- [ ] **Step 3: 最小実装を書く**

`core-toolkit/src/core_toolkit/error_mapping_lifespan.py`:

```python
"""ErrorCodeRegistryのlifespan管理。

``exception_handlers``とASGIミドルウェアはどちらもFastAPIの``Depends``
注入の対象外であるため、``app.state``を共有領域として使う（詳細は
``docs/superpowers/specs/2026-09-28-error-handling-design.md``参照）。
シングルトンのlifespan resourceであり、DB/Redis/ObjectStorageのような
名前付き複数インスタンスにはしない（1アプリ内で``error_code``名前空間を
複数持ちたい実需がないため）。
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette

from core_toolkit.error_mapping import ErrorCodeMapping, ErrorCodeRegistry
from core_toolkit.lifespan import LifespanResource

__all__ = [
    "ErrorMappingLifespanResource",
    "get_error_mapping_resource",
    "open_error_mapping",
]


class ErrorMappingLifespanResource(LifespanResource):
    """シングルトンの``ErrorCodeRegistry``を``app.state.error_mapping``に登録する。

    Args:
        mappings: ``error_code``をキーとした``ErrorCodeMapping``の辞書。
            省略時は空のレジストリになり、全ての例外がデフォルト値
            （``SystemProblem``→500 / それ以外→400）に解決される。
    """

    def __init__(self, mappings: dict[str, ErrorCodeMapping] | None = None) -> None:
        self.registry = ErrorCodeRegistry(mappings)

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        app.state.error_mapping = self
        yield self


def open_error_mapping(
    mappings: dict[str, ErrorCodeMapping] | None = None,
) -> ErrorMappingLifespanResource:
    """``ErrorMappingLifespanResource``を生成するファクトリ。

    将来「設定値管理部品」ができた場合は、
    ``open_error_mapping(mappings=load_from_config(...))``のように
    ``mappings``の構築元だけを差し替える。``ErrorCodeRegistry``自体の
    変更は不要。

    Args:
        mappings: ``error_code``をキーとした``ErrorCodeMapping``の辞書。

    Returns:
        ``create_lifespan(...)``に渡せる``ErrorMappingLifespanResource``。
    """
    return ErrorMappingLifespanResource(mappings)


def get_error_mapping_resource(app: Starlette) -> ErrorMappingLifespanResource:
    """``app.state.error_mapping``からリソースを取得する。

    ``exception_handlers``とASGIミドルウェアは``Request``を経由しない
    箇所からも呼ばれうるため、``Request``ではなく``Starlette``アプリ本体
    から直接取得する（``exception_handler``なら``request.app``、ASGI
    ミドルウェアなら``scope["app"]``を渡す）。

    Args:
        app: Starletteアプリケーションインスタンス。

    Returns:
        登録済みの``ErrorMappingLifespanResource``。

    Raises:
        RuntimeError: 対応する``ErrorMappingLifespanResource``が
            ``create_lifespan(...)``に登録されていない場合。
    """
    if not hasattr(app.state, "error_mapping"):
        raise RuntimeError(
            "app.state.error_mapping is not set. "
            "Did you forget to register ErrorMappingLifespanResource() "
            "(via open_error_mapping()) in create_lifespan(...)?"
        )
    resource: ErrorMappingLifespanResource = app.state.error_mapping
    return resource
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run pytest tests/test_error_mapping_lifespan.py -v`
Expected: 全件PASS

- [ ] **Step 5: リントを通す**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run ruff check src/core_toolkit/error_mapping_lifespan.py tests/test_error_mapping_lifespan.py && uv run ruff format --check src/core_toolkit/error_mapping_lifespan.py tests/test_error_mapping_lifespan.py`
Expected: エラーなし

- [ ] **Step 6: パッケージ全体のテストを実行して既存機能に影響がないことを確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/core-toolkit && uv run pytest`
Expected: 全件PASS

- [ ] **Step 7: コミット**

```bash
cd /home/ubuntu/py-workspace/faststar
git add core-toolkit/src/core_toolkit/error_mapping_lifespan.py core-toolkit/tests/test_error_mapping_lifespan.py
git commit -m "feat(core-toolkit): add ErrorMappingLifespanResource singleton lifespan"
```

---

### Task 5: fastapi-toolkit — RFC 7807変換（`exception_handlers` + 安全網ミドルウェア）

**Files:**
- Create: `fastapi-toolkit/src/fastapi_toolkit/error_mapping_lifespan.py`（re-export）
- Create: `fastapi-toolkit/src/fastapi_toolkit/error_handling.py`
- Test: `fastapi-toolkit/tests/test_error_handling.py`

**Interfaces:**
- Consumes: `core_toolkit.errors.ApplicationProblem`（Task 2）、`core_toolkit.error_mapping.ErrorCodeRegistry`（Task 3）、`core_toolkit.error_mapping_lifespan.{ErrorMappingLifespanResource, get_error_mapping_resource, open_error_mapping}`（Task 4）
- Produces: `handle_application_error(request, exc) -> JSONResponse`（`FastAPI(exception_handlers={...})`に登録する）、`ErrorHandlingMiddleware`（ASGIミドルウェア）。アプリ側が組み立てに使う。

**重要な実装上の注意（Global Constraints参照）:** Starletteは「最後に`add_middleware`したものが一番外側」になる。`ErrorHandlingMiddleware`はユーザー定義ミドルウェア（認証・レート制限等）が送出した例外を拾う安全網なので、アプリ側は他の全ての`app.add_middleware(...)`呼び出しの**後**に`app.add_middleware(ErrorHandlingMiddleware)`を呼ぶ必要がある。ルートハンドラ由来の例外はStarletteの`ExceptionMiddleware`（ルーター直前、`exception_handlers`の実体）が常に先に処理するため、`ErrorHandlingMiddleware`まで伝播せず二重処理にはならない。

- [ ] **Step 1: 失敗するテストを書く**

`fastapi-toolkit/tests/test_error_handling.py`:

```python
"""ApplicationProblem系例外のRFC 7807変換テスト。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from core_toolkit.error_mapping import ErrorCodeMapping
from core_toolkit.error_mapping_lifespan import open_error_mapping
from core_toolkit.errors import ApplicationProblem, BusinessProblem, SystemProblem
from core_toolkit.lifespan import create_lifespan
from fastapi_toolkit.error_handling import ErrorHandlingMiddleware, handle_application_error


class _RaisingMiddleware(BaseHTTPMiddleware):
    """ルーターより外側でApplicationProblemを送出するユーザー定義ミドルウェアのダミー。"""

    async def dispatch(self, request: StarletteRequest, call_next):
        if request.url.path == "/raise-in-middleware":
            raise BusinessProblem("business.blocked", "ミドルウェアで拒否")
        return await call_next(request)


def _make_app() -> FastAPI:
    app = FastAPI(
        lifespan=create_lifespan(
            open_error_mapping({"business.not_found": ErrorCodeMapping(http_status=404)})
        ),
        exception_handlers={ApplicationProblem: handle_application_error},
    )

    @app.get("/business")
    async def business_route() -> None:
        raise BusinessProblem("business.not_found", "見つからない")

    @app.get("/system")
    async def system_route() -> None:
        raise SystemProblem("system.unknown", "内部エラー")

    @app.get("/raise-in-middleware")
    async def middleware_route() -> dict[str, str]:
        return {"ok": "true"}

    # _RaisingMiddlewareが先、ErrorHandlingMiddlewareが最後
    # （Starletteは最後にadd_middlewareしたものが一番外側になるため）。
    app.add_middleware(_RaisingMiddleware)
    app.add_middleware(ErrorHandlingMiddleware)
    return app


@pytest.fixture
def client():
    app = _make_app()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestExceptionHandler:
    @pytest.mark.asyncio
    async def test_business_problem_from_route_returns_mapped_status(self, client):
        async with client as c:
            response = await c.get("/business")
        assert response.status_code == 404
        body = response.json()
        assert body["title"] == "business.not_found"
        assert body["detail"] == "見つからない"
        assert body["status"] == 404
        assert response.headers["content-type"] == "application/problem+json"

    @pytest.mark.asyncio
    async def test_system_problem_from_route_falls_back_to_500(self, client):
        async with client as c:
            response = await c.get("/system")
        assert response.status_code == 500
        assert response.json()["title"] == "system.unknown"


class TestErrorHandlingMiddleware:
    @pytest.mark.asyncio
    async def test_application_problem_from_middleware_is_caught_by_safety_net(self, client):
        async with client as c:
            response = await c.get("/raise-in-middleware")
        assert response.status_code == 400
        body = response.json()
        assert body["title"] == "business.blocked"
        assert body["detail"] == "ミドルウェアで拒否"

    @pytest.mark.asyncio
    async def test_route_exception_does_not_reach_safety_net(self, client, monkeypatch):
        """ルートハンドラ由来の例外はexception_handlersのみが処理し、
        ErrorHandlingMiddleware側の変換処理（共通ヘルパー
        _build_problem_response）は呼ばれない（＝二重処理にならない）
        ことを、共通ヘルパーの呼び出し回数で直接確認する。
        """
        import fastapi_toolkit.error_handling as error_handling_module

        call_count = 0
        original = error_handling_module._build_problem_response

        def _counting_build_problem_response(exc, registry):
            nonlocal call_count
            call_count += 1
            return original(exc, registry)

        monkeypatch.setattr(
            error_handling_module,
            "_build_problem_response",
            _counting_build_problem_response,
        )

        async with client as c:
            response = await c.get("/business")

        assert response.status_code == 404
        assert call_count == 1
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastapi-toolkit && uv run pytest tests/test_error_handling.py -v`
Expected: `ModuleNotFoundError: No module named 'fastapi_toolkit.error_handling'` でFAIL

- [ ] **Step 3: re-exportモジュールを書く**

`fastapi-toolkit/src/fastapi_toolkit/error_mapping_lifespan.py`:

```python
"""ErrorCodeRegistry lifespanのFastAPI向け薄いラッパー。

実装はFastAPIに依存せず``core_toolkit.error_mapping_lifespan``にある。
このモジュールは再エクスポートするだけ。
"""

from core_toolkit.error_mapping import ErrorCodeMapping, ErrorCodeRegistry
from core_toolkit.error_mapping_lifespan import (
    ErrorMappingLifespanResource,
    get_error_mapping_resource,
    open_error_mapping,
)

__all__ = [
    "ErrorCodeMapping",
    "ErrorCodeRegistry",
    "ErrorMappingLifespanResource",
    "get_error_mapping_resource",
    "open_error_mapping",
]
```

- [ ] **Step 4: exception_handlerと安全網ミドルウェアを書く**

`fastapi-toolkit/src/fastapi_toolkit/error_handling.py`:

```python
"""ApplicationProblem系例外をRFC 7807形式のレスポンスに変換する。

``exception_handlers``（ルートハンドラ由来の主経路）と
``ErrorHandlingMiddleware``（ユーザー定義ミドルウェア由来の安全網）の
二層構成。StarletteのExceptionMiddlewareがルーター内で解決するため、
ルートハンドラ由来の例外は``exception_handlers``が処理し、安全網
ミドルウェアには伝播しない（二重処理にはならない）。詳細は
``docs/superpowers/specs/2026-09-28-error-handling-design.md``参照。
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core_toolkit.error_mapping import ErrorCodeRegistry
from core_toolkit.error_mapping_lifespan import get_error_mapping_resource
from core_toolkit.errors import ApplicationProblem, BusinessProblem, SystemProblem

__all__ = [
    "ApplicationProblem",
    "BusinessProblem",
    "ErrorHandlingMiddleware",
    "SystemProblem",
    "handle_application_error",
]


def _build_problem_response(
    exc: ApplicationProblem, registry: ErrorCodeRegistry
) -> JSONResponse:
    status = registry.resolve_http_status(exc)
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


async def handle_application_error(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI/Starletteの``exception_handlers``に登録するハンドラ。

    Example::

        app = FastAPI(
            exception_handlers={ApplicationProblem: handle_application_error},
        )

    Args:
        request: 発生元のリクエスト。
        exc: ``ApplicationProblem``（またはそのサブクラス）のインスタンス。
            Starletteの``exception_handlers``の型シグネチャに合わせて
            ``Exception``として受け取るが、``exception_handlers``に
            ``ApplicationProblem``で登録する限り実際にはサブクラスのみが渡る。

    Returns:
        RFC 7807形式の``JSONResponse``。
    """
    assert isinstance(exc, ApplicationProblem)
    resource = get_error_mapping_resource(request.app)
    return _build_problem_response(exc, resource.registry)


class ErrorHandlingMiddleware:
    """ユーザー定義ミドルウェア由来の``ApplicationProblem``を拾う安全網ASGIミドルウェア。

    StarletteのExceptionMiddlewareはルーター内で解決するため、ルート
    ハンドラ由来の例外は``exception_handlers``が処理し、ここには伝播
    しない。認証・レート制限等、ルーターより外側のユーザー定義
    ミドルウェアが送出した``ApplicationProblem``だけをここで拾う。

    Starletteは最後に``add_middleware``したものが一番外側になるため、
    このミドルウェアは他の全ての``app.add_middleware(...)``呼び出しの
    **後**に登録する必要がある。

    Args:
        app: ラップ対象のASGIアプリケーション。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except ApplicationProblem as exc:
            if started:
                # レスポンス送信が既に始まっていれば新規レスポンスを
                # 差し替えられないため、握りつぶさず再送出する。
                raise
            resource = get_error_mapping_resource(scope["app"])
            response = _build_problem_response(exc, resource.registry)
            await response(scope, receive, send)
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastapi-toolkit && uv run pytest tests/test_error_handling.py -v`
Expected: 全件PASS

- [ ] **Step 6: リントを通す**

Run: `cd /home/ubuntu/py-workspace/faststar/fastapi-toolkit && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: エラーなし

- [ ] **Step 7: パッケージ全体のテストを実行する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastapi-toolkit && uv run pytest`
Expected: 全件PASS

- [ ] **Step 8: コミット**

```bash
cd /home/ubuntu/py-workspace/faststar
git add fastapi-toolkit/src/fastapi_toolkit/error_mapping_lifespan.py \
        fastapi-toolkit/src/fastapi_toolkit/error_handling.py \
        fastapi-toolkit/tests/test_error_handling.py
git commit -m "feat(fastapi-toolkit): add RFC 7807 exception_handler and safety-net middleware"
```

---

### Task 6: fastmcp-toolkit — `CallToolResult`変換ミドルウェア

**Files:**
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/middleware/error_handling.py`
- Modify: `fastmcp-toolkit/src/fastmcp_toolkit/middleware/__init__.py`
- Test: `fastmcp-toolkit/tests/test_middleware_error_handling.py`

**Interfaces:**
- Consumes: `core_toolkit.errors.{ApplicationProblem, BusinessProblem}`（Task 2）
- Produces: `ErrorHandlingMiddleware`（FastMCP `Middleware`）。Task 7が`run_server`に組み込む。

fastmcp-toolkitはMCPプロトコルの制約（`CallToolResult.isError`が2値のみ、JSON-RPCの`error`はメソッド不明等ごく一部のプロトコルレベルのケース専用）により`ErrorCodeRegistry`を持たない。`isinstance`によるログレベル分岐と、例外インスタンス自身が持つ`error_code`/`message`をテキスト化するだけで完結させる。

既存の`ErrorLoggerMiddleware`（`fastmcp_toolkit/middleware/exception_handler.py`）は全例外をログしてre-raiseするだけで、`ApplicationProblem`固有の変換は行わない。この`ErrorHandlingMiddleware`は`ApplicationProblem`のみを捕捉し、それ以外の例外はre-raiseせず素通りさせる（`try`ブロックに`except ApplicationProblem`しか置かない）ことで、`ErrorLoggerMiddleware`と役割分担する（詳細はGlobal Constraints参照）。

- [ ] **Step 1: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_middleware_error_handling.py`:

```python
"""ErrorHandlingMiddlewareのテスト。"""

from functools import partial

import pytest
import structlog
import structlog.testing
from fastmcp.server.middleware import MiddlewareContext
from mcp import types as mt

from core_toolkit.errors import BusinessProblem, SystemProblem
from fastmcp_toolkit.logging import _add_application_id, setup_logging
from fastmcp_toolkit.middleware import ErrorLoggerMiddleware
from fastmcp_toolkit.middleware.error_handling import ErrorHandlingMiddleware

_pre_chain = [
    structlog.contextvars.merge_contextvars,
    partial(_add_application_id, _application_id="test"),
]


def _make_context(
    method: str = "tools/call",
    message: mt.CallToolRequestParams | None = None,
) -> MiddlewareContext[mt.CallToolRequestParams]:
    if message is None:
        message = mt.CallToolRequestParams(name="test_tool")
    return MiddlewareContext(message=message, method=method, type="request")


class TestErrorHandlingMiddleware:
    @pytest.mark.asyncio
    async def test_no_exception_passes_through(self):
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            return "success"

        result = await middleware.on_message(context, call_next)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_business_problem_becomes_call_tool_result_with_is_error(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise BusinessProblem("business.invalid_input", "入力が不正")

        result = await middleware.on_message(context, call_next)

        assert isinstance(result, mt.CallToolResult)
        assert result.isError is True
        assert result.content == [
            mt.TextContent(type="text", text="[business.invalid_input] 入力が不正")
        ]

    @pytest.mark.asyncio
    async def test_business_problem_logs_at_warning_level(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise BusinessProblem("business.invalid_input", "入力が不正")

        with structlog.testing.capture_logs(_pre_chain) as logs:
            await middleware.on_message(context, call_next)

        assert len(logs) == 1
        assert logs[0]["log_level"] == "warning"
        assert logs[0]["exc_type"] == "BusinessProblem"

    @pytest.mark.asyncio
    async def test_system_problem_logs_at_error_level(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise SystemProblem("system.db_unavailable", "DB接続に失敗")

        with structlog.testing.capture_logs(_pre_chain) as logs:
            result = await middleware.on_message(context, call_next)

        assert result.isError is True
        assert logs[0]["log_level"] == "error"

    @pytest.mark.asyncio
    async def test_non_application_problem_is_reraised_without_conversion(self):
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise ValueError("想定外のバグ")

        with pytest.raises(ValueError, match="想定外のバグ"):
            await middleware.on_message(context, call_next)


class TestInteractionWithErrorLoggerMiddleware:
    """ErrorLoggerMiddlewareを外側、ErrorHandlingMiddlewareを内側に置いた
    ときの役割分担を検証する。

    FastMCPの実際のミドルウェアチェーン構築は``for mw in
    reversed(self.middleware)``であり、**内側に置いたミドルウェアが先に
    例外を見る**（外側は内側が再raiseした後の例外しか受け取れない）。
    そのため、``ErrorHandlingMiddleware``を``ErrorLoggerMiddleware``より
    外側に置くと、``ApplicationProblem``は必ず先に内側の
    ``ErrorLoggerMiddleware``でログ＆re-raiseされてから外側に届いてしまい
    二重ログになる。ここでは正しい配置（``ErrorLoggerMiddleware``が外側、
    ``ErrorHandlingMiddleware``が内側）でチェーンを手動で再現し、
    ``ApplicationProblem``が外側の``ErrorLoggerMiddleware``まで
    例外として届かないことを検証する。
    """

    @pytest.mark.asyncio
    async def test_application_problem_does_not_reach_outer_error_logger(self):
        setup_logging(application_id="test")
        error_logger = ErrorLoggerMiddleware()
        error_handling = ErrorHandlingMiddleware()
        context = _make_context()

        async def innermost(ctx):
            raise BusinessProblem("business.invalid_input", "入力が不正")

        async def call_next_through_error_handling(ctx):
            return await error_handling.on_message(ctx, innermost)

        with structlog.testing.capture_logs(_pre_chain) as logs:
            result = await error_logger.on_message(context, call_next_through_error_handling)

        assert result.isError is True
        # ErrorHandlingMiddleware(内側)がApplicationProblemを変換して
        # 正常returnするため、外側のErrorLoggerMiddlewareには例外が
        # 伝播せず、ErrorLoggerMiddleware側のログ（logger_name=
        # "error_logger"）は発生しない。ErrorHandlingMiddleware側の
        # ログ（BusinessProblemなのでwarningレベル）のみが記録される。
        assert len(logs) == 1
        assert logs[0]["log_level"] == "warning"
        assert logs[0]["exc_type"] == "BusinessProblem"

    @pytest.mark.asyncio
    async def test_other_exception_still_reaches_outer_error_logger(self):
        setup_logging(application_id="test")
        error_logger = ErrorLoggerMiddleware()
        error_handling = ErrorHandlingMiddleware()
        context = _make_context()

        async def innermost(ctx):
            raise ValueError("想定外のバグ")

        async def call_next_through_error_handling(ctx):
            return await error_handling.on_message(ctx, innermost)

        with structlog.testing.capture_logs(_pre_chain) as logs:
            with pytest.raises(ValueError, match="想定外のバグ"):
                await error_logger.on_message(context, call_next_through_error_handling)

        # ErrorHandlingMiddleware(内側)はApplicationProblem以外を
        # 変換せず素通りさせるため、外側のErrorLoggerMiddlewareが
        # 拾ってログし、最終的にそのままraiseされる。
        assert len(logs) == 1
        assert logs[0]["exc_type"] == "ValueError"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastmcp-toolkit && uv run pytest tests/test_middleware_error_handling.py -v`
Expected: `ModuleNotFoundError: No module named 'fastmcp_toolkit.middleware.error_handling'` でFAIL

- [ ] **Step 3: 最小実装を書く**

`fastmcp-toolkit/src/fastmcp_toolkit/middleware/error_handling.py`:

```python
"""ApplicationProblem系例外をCallToolResultに変換するFastMCP Middleware。"""

from typing import Any

import structlog
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp import types as mt

from core_toolkit.errors import ApplicationProblem, BusinessProblem

__all__ = ["ErrorHandlingMiddleware"]


class ErrorHandlingMiddleware(Middleware):
    """``ApplicationProblem``系例外を``CallToolResult(isError=True)``に変換する。

    MCPツール呼び出しの失敗は``CallToolResult.isError: bool``という2値
    でしか表現できず、JSON-RPCの``error``（-32xxx）はメソッド不明等ごく
    一部のプロトコルレベルのケース専用であるため、fastapi-toolkitのような
    error_code→jsonrpc_codeの変換テーブルは持たない（詳細は設計doc
    ``docs/superpowers/specs/2026-09-28-error-handling-design.md``参照）。

    ``fastmcp_toolkit.middleware.ErrorLoggerMiddleware``より後に
    ``add_middleware``する（＝オニオン構造でErrorLoggerMiddlewareより
    内側に置く）必要がある。FastMCPのミドルウェアは内側が先に例外を見る
    オニオン構造（外側は内側が再raiseした後の例外しか受け取れない）ため、
    このMiddlewareが外側にあると``ApplicationProblem``が必ず先に
    ``ErrorLoggerMiddleware``でログされてから届いてしまい、二重ログに
    なる。内側に置き、かつ``ApplicationProblem``をここで完結させ
    （re-raiseしない）ことで、``ApplicationProblem``は外側の
    ``ErrorLoggerMiddleware``には例外として届かずログされない。
    ``ErrorLoggerMiddleware``は、このMiddlewareが変換しなかった
    （＝``ApplicationProblem``以外の）真に想定外の例外だけを拾う
    最終防波堤として機能する。

    Args:
        logger_name: structlogロガーインスタンスの名前。
    """

    def __init__(self, logger_name: str = "error_handling") -> None:
        self.logger: structlog.stdlib.BoundLogger = structlog.get_logger(
            logger_name, log_type="exception"
        )

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """``ApplicationProblem``を捕捉し``CallToolResult``へ変換する。

        Args:
            context: ミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            下流ハンドラの戻り値。``ApplicationProblem``発生時は
            ``CallToolResult(isError=True, ...)``。

        Raises:
            Exception: ``ApplicationProblem``以外の例外はそのまま再送出する。
        """
        try:
            return await call_next(context)
        except ApplicationProblem as exc:
            log = (
                self.logger.warning
                if isinstance(exc, BusinessProblem)
                else self.logger.error
            )
            log(
                f"{exc.error_code}: {exc.message}",
                method=context.method,
                exc_type=type(exc).__name__,
            )
            return mt.CallToolResult(
                isError=True,
                content=[
                    mt.TextContent(type="text", text=f"[{exc.error_code}] {exc.message}")
                ],
            )
```

- [ ] **Step 4: `middleware/__init__.py`にexportを追加する**

`fastmcp-toolkit/src/fastmcp_toolkit/middleware/__init__.py`を編集し、`ErrorHandlingMiddleware`のimportと`__all__`への追加を行う：

```python
"""FastMCPアプリケーション向けミドルウェアユーティリティ。"""

from core_toolkit.middleware import AccessLogMiddleware

from fastmcp_toolkit.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp_toolkit.middleware.exception_handler import ErrorLoggerMiddleware
from fastmcp_toolkit.middleware.log_context import LogContextMiddleware
from fastmcp_toolkit.middleware.timer_test import TimerTest
from fastmcp_toolkit.middleware.tool_visibility import ToolVisibilityMiddleware

__all__ = [
    "AccessLogMiddleware",
    "ErrorHandlingMiddleware",
    "ErrorLoggerMiddleware",
    "LogContextMiddleware",
    "TimerTest",
    "ToolVisibilityMiddleware",
]
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastmcp-toolkit && uv run pytest tests/test_middleware_error_handling.py -v`
Expected: 全件PASS

- [ ] **Step 6: リントを通す**

Run: `cd /home/ubuntu/py-workspace/faststar/fastmcp-toolkit && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: エラーなし

- [ ] **Step 7: パッケージ全体のテストを実行する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastmcp-toolkit && uv run pytest`
Expected: 全件PASS

- [ ] **Step 8: コミット**

```bash
cd /home/ubuntu/py-workspace/faststar
git add fastmcp-toolkit/src/fastmcp_toolkit/middleware/error_handling.py \
        fastmcp-toolkit/src/fastmcp_toolkit/middleware/__init__.py \
        fastmcp-toolkit/tests/test_middleware_error_handling.py
git commit -m "feat(fastmcp-toolkit): add ErrorHandlingMiddleware for CallToolResult conversion"
```

---

### Task 7: fastmcp-toolkit — `run_server`への組み込み（ErrorLoggerMiddlewareより内側への登録）

**Files:**
- Modify: `fastmcp-toolkit/src/fastmcp_toolkit/server.py`

**Interfaces:**
- Consumes: `fastmcp_toolkit.middleware.ErrorHandlingMiddleware`（Task 6）
- Produces: `run_server(...)`が`ErrorHandlingMiddleware`を`ErrorLoggerMiddleware`より内側（`ErrorLoggerMiddleware`の直後、`ToolVisibilityMiddleware`の前）に登録する

FastMCPは`for mw in reversed(self.middleware)`でチェーンを構築するため、**最初に`add_middleware`したものが一番外側**になり、内側に置いたミドルウェアが先に例外を見る。`ErrorHandlingMiddleware`は`ApplicationProblem`をここで完結させる（re-raiseしない）ことで外側の`ErrorLoggerMiddleware`への二重ログを避ける設計のため（Global Constraints参照）、`ErrorLoggerMiddleware`より**後**（＝内側）に`add_middleware`する。現状`run_server`は`LogContextMiddleware`→`ErrorLoggerMiddleware`→`ToolVisibilityMiddleware`の順で`add_middleware`しているため、`ErrorLoggerMiddleware`の直後に挿入する。

- [ ] **Step 1: `run_server`の該当箇所を変更する**

`fastmcp-toolkit/src/fastmcp_toolkit/server.py`のimportに追加：

```python
from fastmcp_toolkit.middleware import (
    ErrorHandlingMiddleware,
    ErrorLoggerMiddleware,
    LogContextMiddleware,
)
```

`app.add_middleware(ErrorLoggerMiddleware())`の**後**、`app.add_middleware(ToolVisibilityMiddleware(...))`の**前**に`ErrorHandlingMiddleware()`を追加：

```python
    app.add_middleware(LogContextMiddleware())
    app.add_middleware(ErrorLoggerMiddleware())
    app.add_middleware(ErrorHandlingMiddleware())
    app.add_middleware(
        ToolVisibilityMiddleware(
            application_config.invisible_target_prefix,
            application_config.invisible_target_suffix,
        )
    )
```

（`LogContextMiddleware`・`ErrorLoggerMiddleware`の既存の呼び出し順序、および`ToolVisibilityMiddleware`以降の既存コードは変更しない。）

- [ ] **Step 2: importを確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastmcp-toolkit && uv run python3 -c "import fastmcp_toolkit.server"`
Expected: エラーなくimportできる（`run_server`自体はuvicornをブロック起動するため、importの成否のみを確認する。既存の`server.py`にも専用のユニットテストが無いことと一貫する）

- [ ] **Step 3: リントを通す**

Run: `cd /home/ubuntu/py-workspace/faststar/fastmcp-toolkit && uv run ruff check src/fastmcp_toolkit/server.py && uv run ruff format --check src/fastmcp_toolkit/server.py`
Expected: エラーなし

- [ ] **Step 4: パッケージ全体のテストを実行し、既存のミドルウェア関連テストに影響がないことを確認する**

Run: `cd /home/ubuntu/py-workspace/faststar/fastmcp-toolkit && uv run pytest`
Expected: 全件PASS

- [ ] **Step 5: コミット**

```bash
cd /home/ubuntu/py-workspace/faststar
git add fastmcp-toolkit/src/fastmcp_toolkit/server.py
git commit -m "feat(fastmcp-toolkit): register ErrorHandlingMiddleware inside ErrorLoggerMiddleware in run_server"
```

---

## 本計画のスコープ外（設計docの「未決事項」を踏襲）

以下は設計doc `docs/superpowers/specs/2026-09-28-error-handling-design.md` の「未決事項」節に記載の通り、本計画では対応しない：

- `error_code`の命名規則（ドット区切り階層にするか等）
- `msal_errors.py`（`MsalTokenError`系）を`SystemProblem`のサブクラスとして統合するか、独立のまま残すか
- ログ記録（structlog / OpenTelemetry span）を`exception_handler`側と安全網ミドルウェア側のどちらの責務にするか、二重記録防止の一般則の確立（本計画ではfastmcp-toolkit側の`ErrorHandlingMiddleware`/`ErrorLoggerMiddleware`間の二重記録防止のみ対応済み）
- `ErrorCodeRegistry`の登録漏れ（未登録`error_code`がデフォルト400/500に落ちる）を起動時やテストでどう検知するか
- fastmcp-toolkit側で将来`structuredContent`を使う設計に発展させる必要が出た場合の再検討
- 「設定値管理部品」本体の設計（別Issueで検討予定）
