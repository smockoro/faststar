"""MCP向けCORSミドルウェア設定ファクトリ。"""

from typing import Any

__all__ = ["mcp_cors_options"]

_DEFAULT_ALLOW_HEADERS: list[str] = [
    "Content-Type",
    "Authorization",
    "Mcp-Session-Id",
    "MCP-Protocol-Version",
    "Accept",
    "Last-Event-ID",
]


def mcp_cors_options(
    *,
    allow_origins: list[str],
    allow_headers: list[str] | None = None,
    max_age: int = 10,
    allow_credentials: bool = True,
) -> dict[str, Any]:
    """MCPサーバー向けCORSMiddleware設定を生成するファクトリ関数。

    Starlette/FastAPIの ``CORSMiddleware`` に渡すkwargsを返す。
    MCP仕様およびOWASP推奨に基づくデフォルト値を適用する。

    Args:
        allow_origins: 許可するオリジンのリスト。
            ワイルドカード ``"*"`` はOrigin検証の趣旨と矛盾するため非推奨。
        allow_headers: 許可するリクエストヘッダーのリスト。
            未指定の場合、MCPプロトコルで使用されるヘッダーを許可する。
        max_age: プリフライトレスポンスのキャッシュ秒数。
            認証ありの場合は短い値（10秒）が推奨される。
        allow_credentials: 認証情報（Cookie, Authorizationヘッダー等）を許可するか。

    Returns:
        ``CORSMiddleware`` コンストラクタに展開可能なdict。

    Example::

        from starlette.middleware.cors import CORSMiddleware
        from fastmcp_toolkit.cors import mcp_cors_options

        app.add_middleware(
            CORSMiddleware,
            **mcp_cors_options(allow_origins=["https://example.com"]),
        )
    """
    return {
        "allow_origins": allow_origins,
        "allow_methods": ["GET", "POST", "DELETE", "OPTIONS"],
        "allow_headers": (
            allow_headers if allow_headers is not None else _DEFAULT_ALLOW_HEADERS
        ),
        "expose_headers": ["Mcp-Session-Id"],
        "allow_credentials": allow_credentials,
        "max_age": max_age,
    }
