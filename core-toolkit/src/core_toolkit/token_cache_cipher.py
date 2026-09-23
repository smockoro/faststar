"""トークンキャッシュ（SerializableTokenCache）の暗号化。

JWE（RFC 7516、joserfc実装）による暗号化をデフォルトとする。鍵は``kid``
ごとに複数登録でき、「現在使うkid」を切り替えるだけで新しい鍵/アルゴリズム
へ移行できる。過去に別kidで暗号化されたデータも、そのkidの鍵が登録され
続けていれば復号できる。

利用には ``core-toolkit[msal]`` extraのインストールが必要。
"""

from typing import Protocol

from joserfc import jwe
from joserfc.jwk import OctKey


class TokenCacheCipher(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...


class JweTokenCacheCipher:
    """joserfcベースのJWE（``alg=dir``, ``enc=A256GCM``）によるCipher実装。

    Args:
        keys: ``kid``をキーとする鍵materialの辞書
            （例: ``{"v1": b"...32bytes..."}``）。複数バージョン渡すことで
            鍵ローテーション・アルゴリズム移行に対応する。
        current_kid: 暗号化時に使う``kid``。復号時は暗号文ヘッダの``kid``を
            見て``keys``から自動選択するため、この値には依存しない。
    """

    def __init__(self, keys: dict[str, bytes], current_kid: str) -> None:
        self._keys = {kid: OctKey.import_key(key) for kid, key in keys.items()}
        self._current_kid = current_kid

    def encrypt(self, plaintext: bytes) -> bytes:
        """平文をJWE Compact Serializationとして暗号化する。"""
        key = self._keys[self._current_kid]
        protected = {"alg": "dir", "enc": "A256GCM", "kid": self._current_kid}
        result = jwe.encrypt_compact(protected, plaintext, key)
        return result.encode() if isinstance(result, str) else result

    def decrypt(self, ciphertext: bytes) -> bytes:
        """JWE暗号文を復号する。ヘッダの``kid``に対応する鍵が未登録の場合は``KeyError``。"""

        def resolve_key(recipient) -> OctKey:
            headers = recipient.headers()
            kid = headers["kid"]
            return self._keys[kid]

        if isinstance(ciphertext, str):
            ciphertext = ciphertext.encode()
        obj = jwe.decrypt_compact(ciphertext, resolve_key)
        return obj.plaintext


def default_token_cache_cipher(
    keys: dict[str, bytes], current_kid: str = "v1"
) -> JweTokenCacheCipher:
    """デフォルトのCipherを構築する。

    ``keys``・``current_kid``を環境変数等から組み立てるのは呼び出し側
    （将来の設定値管理機能）の責務とし、ここでは受け取るだけに留める。
    """
    return JweTokenCacheCipher(keys=keys, current_kid=current_kid)
