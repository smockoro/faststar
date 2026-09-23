"""token_cache_cipherの単体テスト。"""

import pytest

from core_toolkit.token_cache_cipher import (
    JweTokenCacheCipher,
    default_token_cache_cipher,
)


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def test_encrypt_then_decrypt_roundtrip():
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    ciphertext = cipher.encrypt(b"secret-token-cache-payload")

    assert cipher.decrypt(ciphertext) == b"secret-token-cache-payload"


def test_ciphertext_does_not_contain_plaintext():
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    ciphertext = cipher.encrypt(b"secret-token-cache-payload")

    assert b"secret-token-cache-payload" not in ciphertext


def test_decrypt_selects_key_by_kid_after_rotation():
    old_cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    ciphertext = old_cipher.encrypt(b"payload-from-v1")

    rotated_cipher = JweTokenCacheCipher(
        keys={"v1": _key(1), "v2": _key(2)}, current_kid="v2"
    )

    assert rotated_cipher.decrypt(ciphertext) == b"payload-from-v1"


def test_encrypt_after_rotation_cannot_be_decrypted_by_old_key_only():
    rotated_cipher = JweTokenCacheCipher(
        keys={"v1": _key(1), "v2": _key(2)}, current_kid="v2"
    )
    ciphertext = rotated_cipher.encrypt(b"payload-from-v2")

    v1_only_cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")

    with pytest.raises(KeyError):
        v1_only_cipher.decrypt(ciphertext)
