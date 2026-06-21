# faststar

Starlette / FastAPI / FastMCP 向けの共通ツールキットモノレポ。

## パッケージ構成

| パッケージ | 対象フレームワーク | 概要 |
|---|---|---|
| [fastmcp-toolkit](./fastmcp-toolkit/) | FastMCP | 構造化ロギング、ミドルウェア、ヘルスチェック、メトリクス、テレメトリ |
| [fastapi-toolkit](./fastapi-toolkit/) | FastAPI | 構造化ロギング、リクエストログミドルウェア、Lifespan管理 |

## 開発環境

- Python 3.14+
- パッケージマネージャ: [uv](https://docs.astral.sh/uv/)
- リンター/フォーマッター: [ruff](https://docs.astral.sh/ruff/)
- テスト: pytest + pytest-asyncio

## 各パッケージの開発

各パッケージは独立した pyproject.toml を持つ。パッケージディレクトリ内で操作する。

```bash
cd fastmcp-toolkit  # or fastapi-toolkit

# 依存インストール
uv sync --all-extras

# テスト
uv run pytest

# リント
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

## パッケージ間の関係

現在 fastmcp-toolkit と fastapi-toolkit は独立している。将来共通モジュール（構造化ロギングの基盤部分など）が必要になった場合、core-toolkit パッケージとして切り出す。

## ビルド・パブリッシュ

各パッケージの `Makefile` を使用する。

```bash
cd fastmcp-toolkit
make publish
```
