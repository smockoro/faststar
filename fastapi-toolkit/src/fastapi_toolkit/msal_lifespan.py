"""MSAL lifespanリソースのFastAPI向け薄いラッパー。

実装はFastAPIに依存せず``core_toolkit.msal_lifespan``にある。このモジュール
は再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[str, Depends(get_msal_app_token("downstream-api", scopes=[...]))]``）
はアプリ側が名前とscopesを指定して定義する。

利用には ``fastapi-toolkit[msal]`` extraのインストールが必要。
"""

from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import (
    MsalClientCredentialLifespanResource,
    MsalOboLifespanResource,
    default_msal_http_session,
    get_msal_app_token,
    get_msal_obo_token,
)
from core_toolkit.token_cache_cipher import (
    JweTokenCacheCipher,
    TokenCacheCipher,
    default_token_cache_cipher,
)

__all__ = [
    "MsalClaimsChallengeError",
    "MsalClientCredentialLifespanResource",
    "MsalOboLifespanResource",
    "MsalTokenError",
    "JweTokenCacheCipher",
    "TokenCacheCipher",
    "default_msal_http_session",
    "default_token_cache_cipher",
    "get_msal_app_token",
    "get_msal_obo_token",
]
