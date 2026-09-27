"""error_code文字列からプロトコル固有値（HTTPステータス等）への変換テーブル。"""

from dataclasses import dataclass

from core_toolkit.errors import ApplicationProblem, SystemProblem

__all__ = ["ErrorCodeMapping", "ErrorCodeRegistry"]


@dataclass(frozen=True)
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
