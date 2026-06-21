# Agents Guide

## 全般

- このリポジトリは複数パッケージを含むモノレポ構造
- 各パッケージ (`fastmcp-toolkit/`, `fastapi-toolkit/`) はそれぞれ独立した pyproject.toml と仮想環境を持つ
- コード変更時は対象パッケージのディレクトリで `uv run ruff check src/ tests/` と `uv run pytest` を実行すること
- ruff のエラーは `uv run ruff check --fix src/ tests/` で自動修正を試み、残りは手動修正
- テストが落ちたらコミット前に修正すること

## ライブラリ設計原則

- 全パッケージはライブラリである。利用者は別リポジトリで開発する他チームの開発者
- 公開API（`__all__` に含まれるもの）を変更する場合は後方互換性を考慮する
- 内部実装は `_` プレフィクスで非公開にする
- 新機能を追加したら `examples/` にサンプルを追加する（examplesがあるパッケージの場合）

## テスト方針

- ユニットテスト: `tests/test_*.py`
- 統合テスト: `tests/integration/test_*.py`（存在する場合）
- examples/ のサンプルはCIでインポートチェックのみ行う

## パッケージ間の関係

- fastmcp-toolkit と fastapi-toolkit は現在独立
- 共通モジュールが必要になった場合は core-toolkit として切り出す
- パッケージ間の依存を追加する場合は README.md に明記する
