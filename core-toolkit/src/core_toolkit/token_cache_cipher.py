"""トークンキャッシュ（SerializableTokenCache）の暗号化。

JWE（RFC 7516、joserfc実装）による暗号化をデフォルトとする。鍵は``kid``
ごとに複数登録でき、「現在使うkid」を切り替えるだけで新しい鍵/アルゴリズム
へ移行できる。過去に別kidで暗号化されたデータも、そのkidの鍵が登録され
続けていれば復号できる。

利用には ``core-toolkit[msal]`` extraのインストールが必要。
"""

from typing import Protocol

from joserfc import jwe
from joserfc.jwk import GuestProtocol, OctKey


class TokenCacheCipher(Protocol):
    """トークンキャッシュの暗号化/復号を行うCipherのインターフェース。

    実装は平文とバイト列の暗号文を相互に変換できればよく、具体的な
    暗号化方式（JWEかどうか等）には依存しない。
    """

    def encrypt(self, plaintext: bytes) -> bytes:
        """平文を暗号化する。"""
        ...

    def decrypt(self, ciphertext: bytes) -> bytes:
        """暗号文を復号する。"""
        ...


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
        # joserfcの型定義上はstr | bytesを返し得るが、実装は常にstrを返すため
        # else節（bytesをそのまま返す分岐）は実行時には通らない。
        return result.encode() if isinstance(result, str) else result

    def decrypt(self, ciphertext: bytes) -> bytes:
        """JWE暗号文を復号する。ヘッダの``kid``に対応する鍵が未登録の場合は``KeyError``。"""

        def resolve_key(recipient: GuestProtocol) -> OctKey:
            headers = recipient.headers()
            kid = headers["kid"]
            return self._keys[kid]

        # 呼び出し側の型はbytes固定だが、str実引数が渡された場合でも
        # 壊れないよう防御的に変換する（このcipherの型注釈上は通常発生しない）。
        if isinstance(ciphertext, str):
            ciphertext = ciphertext.encode()
        # algorithmsはalg/enc両方に対する許可リストとして扱われるため、
        # このcipherが使う"dir"と"A256GCM"を明示することで、鍵ラップ系alg
        # （RSA-OAEP/A*KW/ECDH-ES等）や他のenc（CBC系等）を拒否し、
        # 同一鍵materialを別アルゴリズムで誤解釈するアルゴリズム混同を防ぐ。
        obj = jwe.decrypt_compact(
            ciphertext, resolve_key, algorithms=["dir", "A256GCM"]
        )
        return obj.plaintext


def default_token_cache_cipher(
    keys: dict[str, bytes], current_kid: str = "v1"
) -> JweTokenCacheCipher:
    """デフォルトのCipherを構築する。

    ``keys``・``current_kid``を環境変数等から組み立てるのは呼び出し側
    （将来の設定値管理機能）の責務とし、ここでは受け取るだけに留める。

    Args:
        keys: ``kid``をキーとする鍵materialの辞書
            （例: ``{"v1": b"...32bytes..."}``）。
        current_kid: 暗号化時に使う``kid``。デフォルトは``"v1"``。

    Returns:
        構築された``JweTokenCacheCipher``インスタンス。
    """
    return JweTokenCacheCipher(keys=keys, current_kid=current_kid)
