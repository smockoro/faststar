# fastapi-toolkit

FastAPI アプリケーション向けの共通ユーティリティライブラリ。

## 機能

| モジュール | 提供機能 |
|---|---|
| `setup_logging` | structlogベースの構造化ロギング設定（uvicornログ統一含む） |
| `get_logger` | log_type付きの構造化ロガー取得 |
| `RequestLoggingMiddleware` | リクエスト/レスポンスの構造化ログミドルウェア |
| `create_lifespan` | 複数のLifespanResourceを合成するヘルパー |
| `AioHttpLifespanResource` | aiohttp ClientSessionのライフサイクル管理 |
| `HttpxLifespanResource` | httpx AsyncClientのライフサイクル管理 |

## インストール

```bash
uv add fastapi-toolkit
```

## クイックスタート

### 構造化ロギング + リクエストログ

```python
from fastapi import FastAPI
from fastapi_toolkit import setup_logging, RequestLoggingMiddleware
from fastapi_toolkit.logging import get_logger

setup_logging(application_id="my-api", json_output=True)
logger = get_logger(__name__)

app = FastAPI()
app.add_middleware(RequestLoggingMiddleware)

@app.get("/hello")
async def hello():
    logger.info("processing request")
    return {"message": "hello"}
```

### Lifespan管理

```python
from fastapi import FastAPI
from fastapi_toolkit.lifespan import create_lifespan, HttpxLifespanResource

app = FastAPI(lifespan=create_lifespan(HttpxLifespanResource()))

@app.get("/fetch")
async def fetch(request):
    client = request.app.state.http_client
    resp = await client.get("https://example.com")
    return {"status": resp.status_code}
```

## 開発

```bash
# 依存インストール
uv sync --all-extras

# テスト
uv run pytest

# リント・フォーマット
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

## 要件

- Python 3.14+
- FastAPI >= 0.115
- structlog >= 24.0
