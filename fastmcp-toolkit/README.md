# fastmcp-toolkit

FastMCP アプリケーション向けのプロダクションレディなユーティリティライブラリ。

## 機能

| モジュール | 提供機能 |
|---|---|
| `setup_logging` | structlogベースの構造化ロギング設定 |
| `run_server` | ミドルウェア・ヘルスチェック・メトリクス込みのサーバー起動 |
| `AccessLogMiddleware` | ASGI層のリクエスト/レスポンス構造化ログ |
| `LogContextMiddleware` | MCP request_id/session_id/method をcontextvarsにバインド |
| `ErrorLoggerMiddleware` | 未処理例外のstructlogロギング |
| `ToolVisibilityMiddleware` | ツール一覧から特定ツールを非表示化 |
| `ToolMetricsMiddleware` | ツール呼び出し回数とレイテンシの自動計測（Prometheus） |
| `register_health_endpoints` | Kubernetes向け /livez, /readyz エンドポイント |
| `register_metrics_endpoint` | Prometheusスクレイプ用 /metrics エンドポイント |
| `ApplicationMeterHelper` | OTELメトリクスを1行で記録するヘルパー |
| `setup_telemetry` | OpenTelemetry TracerProvider設定 |
| `@span` | 関数にOTELスパンを付与するデコレータ |

## インストール

```bash
uv add fastmcp-toolkit
```

テレメトリ機能を使う場合:

```bash
uv add "fastmcp-toolkit[telemetry]"
```

## クイックスタート

### 最小構成

```python
from fastmcp import FastMCP
from fastmcp_toolkit import run_server
from fastmcp_toolkit.logging import get_logger

logger = get_logger(__name__)
app = FastMCP("my-server")

@app.tool()
def hello(name: str) -> str:
    logger.info("tool invoked", name=name)
    return f"Hello, {name}!"

if __name__ == "__main__":
    run_server(app, json_output=False)
```

`run_server` は以下を自動設定する:

- structlog構造化ロギング（application_id付き）
- AccessLogMiddleware（ASGI層）
- LogContextMiddleware（MCP request_id等のバインド）
- ErrorLoggerMiddleware（例外ログ）
- ToolVisibilityMiddleware（ツール非表示制御）
- uvicornのデフォルトアクセスログ無効化

### ロギング単体利用

```python
from fastmcp_toolkit import setup_logging
from fastmcp_toolkit.logging import get_logger, bind_contextvars

setup_logging(application_id="my-app", json_output=True)
logger = get_logger(__name__)

bind_contextvars(user="alice")
logger.info("processing", key="value")
```

### ヘルスチェック

```python
async def check_database() -> tuple[bool, str]:
    return True, "connected"

run_server(app, health_check=True, readiness_checks=[check_database])
```

### テレメトリ

```python
from fastmcp_toolkit.telemetry import setup_telemetry
from fastmcp_toolkit.decorator import span

setup_telemetry(service_name="my-server")  # FastMCPをimportする前に呼ぶ

@span
async def inner_logic():
    ...
```

## 環境変数による設定

| 変数名 | デフォルト | 説明 |
|---|---|---|
| `CONFIG_TYPE` | `env` | 設定の読み込み元（`env` or `yaml`） |
| `INVISIBLE_TARGET_PREFIX` | (空) | 非表示ツールのプレフィックス（カンマ区切り） |
| `INVISIBLE_TARGET_SUFFIX` | (空) | 非表示ツールのサフィックス（カンマ区切り） |
| `METRICS_ENABLED` | `true` | Prometheusメトリクスの有効/無効 |
| `METRICS_ENDPOINT` | `/metrics` | メトリクスエンドポイントのパス |
| `HEALTH_CHECK_ENABLED` | `true` | ヘルスチェックの有効/無効 |
| `HEALTH_CHECK_LIVENESS_ENDPOINT` | `/readyz` | Livenessエンドポイントのパス |
| `HEALTH_CHECK_READINESS_ENDPOINT` | `/livez` | Readinessエンドポイントのパス |

## 開発

```bash
# 依存インストール
uv sync --all-extras

# テスト
uv run pytest

# リント・フォーマット
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# フォーマット適用
uv run ruff format src/ tests/
uv run ruff check --fix src/ tests/
```

OTELの検証には [otel-desktop-viewer](https://github.com/CtrlSpice/otel-desktop-viewer) を利用する。

## 要件

- Python 3.14+
- fastmcp >= 2.0
- structlog >= 24.0
