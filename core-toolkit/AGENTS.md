# core-toolkit

Starlette/ASGI共通ミドルウェアパッケージ。

## 開発コマンド

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## 方針

- Pure ASGI ミドルウェアとして実装する（BaseHTTPMiddleware は使わない）
- フレームワーク（FastAPI, FastMCP）への依存を持たない
- structlog のみ外部依存として許可
