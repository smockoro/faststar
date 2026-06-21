"""アプリケーション設定。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv() -> None:
    """プロジェクトルートの .env を読み込む。

    既に環境変数として設定されている値は上書きしない（override=False）。
    """
    env_path = Path.cwd() / ".env"
    load_dotenv(env_path, override=False)


_load_dotenv()


@dataclass(frozen=True)
class ApplicationSettings:
    """EntraID接続などアプリケーションに必要な設定値。

    .env ファイル → 環境変数 → デフォルト値 の優先順で解決する。
    """

    host: str = field(default_factory=lambda: os.environ.get("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", 8000)))

    client_id: str = field(default_factory=lambda: os.environ.get("AZURE_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.environ.get("AZURE_CLIENT_SECRET", ""))
    tenant_id: str = field(default_factory=lambda: os.environ.get("AZURE_TENANT_ID", ""))
    redirect_uri: str = field(
        default_factory=lambda: os.environ.get("AZURE_REDIRECT_URI", "http://localhost:8000/auth/callback")
    )
    scopes: list[str] = field(
        default_factory=lambda: os.environ.get("AZURE_SCOPES", "openid,profile,email").split(",")
    )
    backend_b_scopes: list[str] = field(
        default_factory=lambda: os.environ.get("AZURE_BACKEND_B_SCOPES", "").split(",")
    )
    session_secret: str = field(default_factory=lambda: os.environ.get("SESSION_SECRET", "dev-secret-change-me"))

    backend_a_url: str = field(default_factory=lambda: os.environ.get("BACKEND_A_URL", "http://localhost:8001/api/a"))
    backend_b_url: str = field(default_factory=lambda: os.environ.get("BACKEND_B_URL", "http://localhost:8002/api/b"))

    @property
    def authority(self) -> str:
        """OIDCのauthorityエンドポイントを返す。"""
        return f"https://login.microsoftonline.com/{self.tenant_id}"


def load_application_settings() -> ApplicationSettings:
    """設定を読み込む。

    .env が既にモジュールロード時に読み込まれているため、
    ここでは ApplicationSettings を生成するだけ。

    Returns:
        ApplicationSettings: EntraID接続設定
    """
    return ApplicationSettings()
