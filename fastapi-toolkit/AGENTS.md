# Agents Guide - fastapi-toolkit

## 概要

FastAPIアプリケーション向けの共通ユーティリティライブラリ。

## 開発時の注意

- コード変更後は必ず `uv run ruff check src/ tests/` と `uv run pytest` を実行
- ruff エラーは `uv run ruff check --fix src/ tests/` で自動修正を試行
- テスト失敗はコミット前に修正すること

## ライブラリ設計原則

- 利用者は FastAPI でWebアプリ/APIを構築する開発者
- `__all__` に含まれる公開APIを変更する場合は後方互換性を考慮
- 内部実装は `_` プレフィクスで非公開化

## アーキテクチャ

- src layout（`src/fastapi_toolkit/`）
- ミドルウェアは Starlette の `BaseHTTPMiddleware` を継承
- Lifespan管理は `LifespanResource` 抽象クラスを実装
- ロギングは structlog + 標準logging統合

## テスト方針

- ユニットテスト: `tests/test_*.py`
- httpx の `ASGITransport` を使ったインプロセステスト
