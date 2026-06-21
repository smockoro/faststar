"""汎用ユーティリティ関数。"""


def parse_str_tuple(string: str) -> tuple:
    """カンマ区切り文字列をタプルに変換する。

    各要素は前後の空白が除去され、空文字列の要素は無視される。

    Args:
        string: カンマ区切りの文字列（例: ``"a, b, c"``）。

    Returns:
        パースされた文字列のタプル。
    """
    return tuple(v.strip() for v in string.split(",") if v.strip())
