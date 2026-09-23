"""MSALのdict形式エラー応答を例外に変換するための共通エラー型。"""


class MsalTokenError(Exception):
    """トークン取得に失敗した場合に送出する。

    Args:
        error: MSALが返す``"error"``の値（例: ``"invalid_client"``）。
        error_description: MSALが返す``"error_description"``の値。
        error_codes: MSALが返す``"error_codes"``の値（AADSTSエラーコード列）。
    """

    def __init__(
        self,
        error: str | None,
        error_description: str | None,
        error_codes: list[int] | None = None,
    ) -> None:
        self.error = error
        self.error_description = error_description
        self.error_codes = error_codes or []
        super().__init__(f"{error}: {error_description}")


class MsalClaimsChallengeError(MsalTokenError):
    """Conditional Access等でユーザーの再認可（MFA等）が必要な場合に送出する。

    Args:
        error: MSALが返す``"error"``の値。
        error_description: MSALが返す``"error_description"``の値。
        claims: 再認可フローのauthorize URLへそのまま付与する必要がある値。
        error_codes: MSALが返す``"error_codes"``の値。
    """

    def __init__(
        self,
        error: str | None,
        error_description: str | None,
        claims: str,
        error_codes: list[int] | None = None,
    ) -> None:
        super().__init__(error, error_description, error_codes)
        self.claims = claims
