"""MSALトークンキャッシュのインメモリ管理。"""

import msal

_cache_store: dict[str, str] = {}


def get_token_cache(oid: str) -> msal.SerializableTokenCache:
    """ユーザーのOIDに紐づくトークンキャッシュを取得する。

    インメモリの dict にシリアライズ済みキャッシュを保持する。
    存在しなければ新規作成、存在すれば復元して返す。

    Args:
        oid: ユーザーのオブジェクトID（EntraIDのoid クレーム）

    Returns:
        msal.SerializableTokenCache: 該当ユーザーのトークンキャッシュ
    """
    cache = msal.SerializableTokenCache()
    if oid in _cache_store:
        cache.deserialize(_cache_store[oid])
    return cache


def save_token_cache(oid: str, cache: msal.SerializableTokenCache) -> None:
    """トークンキャッシュに変更があればインメモリに永続化する。

    Args:
        oid: ユーザーのオブジェクトID
        cache: 保存対象のトークンキャッシュ
    """
    if cache.has_state_changed:
        _cache_store[oid] = cache.serialize()


def remove_token_cache(oid: str) -> None:
    """ユーザーのトークンキャッシュを削除する。

    Args:
        oid: ユーザーのオブジェクトID
    """
    _cache_store.pop(oid, None)
