# faststar

Starlette / FastAPI / FastMCP 向け共通ツールキットのモノレポ。

## プロジェクト構成

```
faststar/
├── core-toolkit/      Starlette/ASGI共通ミドルウェアライブラリ
│   ├── src/core_toolkit/
│   └── tests/
├── fastmcp-toolkit/   FastMCP向けユーティリティライブラリ（core-toolkitに依存）
│   ├── src/fastmcp_toolkit/
│   ├── tests/
│   └── examples/
├── fastapi-toolkit/   FastAPI向けユーティリティライブラリ（core-toolkitに依存）
│   ├── src/fastapi_toolkit/
│   └── tests/
└── .claude/           Claude Code設定（共通）
```

## 開発コマンド

各パッケージディレクトリで実行する:

```bash
# 依存インストール
uv sync --all-extras

# リント
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# フォーマット適用
uv run ruff format src/ tests/
uv run ruff check --fix src/ tests/

# テスト
uv run pytest
```

## コーディング規約

- Python 3.14+
- ruff でリント・フォーマット（E, F, I, UP ルール）
- 型ヒントを必ず付ける
- ライブラリとして公開するため、公開APIには docstring を日本語・Googleスタイルで付ける
- テストは tests/ 配下に配置

## アーキテクチャ方針

- src layout を使用
- ビルドバックエンドは hatchling
- structlog ベースの構造化ロギング
- 共通ミドルウェアは core-toolkit に集約し、各toolkitから path dependency で参照
- パッケージ間の依存は core-toolkit を介してのみ許可
