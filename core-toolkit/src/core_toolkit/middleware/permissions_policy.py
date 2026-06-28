"""Permissions-Policy ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["PermissionsPolicyMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]

_DEFAULT_FEATURES: dict[str, str] = {
    "camera": "()",
    "microphone": "()",
    "geolocation": "()",
    "payment": "()",
    "usb": "()",
    "interest-cohort": "()",
}


class PermissionsPolicyMiddleware:
    """Permissions-Policyヘッダーを付与するASGIミドルウェア。

    ブラウザの機能（カメラ、マイク、位置情報等）へのアクセスを制御し、
    XSS等で注入されたスクリプトからの機能悪用を防止する。

    Args:
        app: 次のASGIアプリケーション。
        features: 機能名と許可ポリシーのdict。
            キーが機能名、値が許可リスト（例: ``"()"`` で無効化、
            ``"self"`` で同一オリジンのみ許可）。
            未指定の場合、不要な機能をすべて無効化するデフォルトを適用する。

    Example::

        from core_toolkit.middleware import PermissionsPolicyMiddleware

        # デフォルト（全機能無効化）
        app.add_middleware(PermissionsPolicyMiddleware)
        # カスタム
        app.add_middleware(PermissionsPolicyMiddleware, features={
            "camera": "()",
            "microphone": "(self)",
        })
    """

    def __init__(self, app: Any, *, features: dict[str, str] | None = None) -> None:
        self.app = app
        resolved = features if features is not None else _DEFAULT_FEATURES
        self._value = self._build_policy(resolved)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"permissions-policy", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)

    @staticmethod
    def _build_policy(features: dict[str, str]) -> bytes:
        """features dictからPermissions-Policyヘッダー値を構築する。"""
        parts = [f"{name}={value}" for name, value in features.items()]
        return ", ".join(parts).encode()
