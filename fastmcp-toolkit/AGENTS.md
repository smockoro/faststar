# Agents Guide - fastmcp-toolkit

## 概要

FastMCPアプリケーション向けのプロダクションレディなユーティリティライブラリ。

## 開発時の注意

- コード変更後は必ず `uv run ruff check src/ tests/` と `uv run pytest` を実行
- ruff エラーは `uv run ruff check --fix src/ tests/` で自動修正を試行
- テスト失敗はコミット前に修正すること

## ライブラリ設計原則

- 利用者は FastMCP でMCPサーバーを構築する開発者
- `__all__` に含まれる公開APIを変更する場合は後方互換性を考慮
- 内部実装は `_` プレフィクスで非公開化
- 新機能追加時は `examples/` にサンプルを追加

## アーキテクチャ

- src layout（`src/fastmcp_toolkit/`）
- ミドルウェアは `fastmcp.server.middleware.Middleware` を継承
- ASGI層ミドルウェア（`AccessLogMiddleware`）は Starlette の add_middleware で追加
- FastMCP層ミドルウェア（`LogContextMiddleware` 等）は `app.add_middleware()` で追加
- 設定は環境変数から `ApplicationConfigProvider` 経由で取得

## テスト方針

- ユニットテスト: `tests/test_*.py`
- 統合テスト: uvicornサーバー起動 + MCPクライアントで疎通確認
- conftest.py に structlog キャプチャ用フィクスチャあり
