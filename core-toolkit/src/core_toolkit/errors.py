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
