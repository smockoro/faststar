"""ApplicationProblem系例外をRFC 7807形式のレスポンスに変換する。

``exception_handlers``（ルートハンドラ由来の主経路）と
``ErrorHandlingMiddleware``（ユーザー定義ミドルウェア由来の安全網）の
二層構成。StarletteのExceptionMiddlewareがルーター内で解決するため、
ルートハンドラ由来の例外は``exception_handlers``が処理し、安全網
ミドルウェアには伝播しない（二重処理にはならない）。詳細は
``docs/superpowers/specs/2026-09-28-error-handling-design.md``参照。
"""

from core_toolkit.error_mapping import ErrorCodeRegistry
from core_toolkit.error_mapping_lifespan import get_error_mapping_resource
from core_toolkit.errors import ApplicationProblem, BusinessProblem, SystemProblem
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "ApplicationProblem",
    "BusinessProblem",
    "ErrorHandlingMiddleware",
    "SystemProblem",
    "handle_application_error",
]


# SystemProblemのmessageはホスト名・SQL・内部ID等を含みうるため、
# クライアントには返さず固定文言に置き換える（原文はログにのみ残す）。
# StarletteのServerErrorMiddlewareの既定500応答（"Internal Server Error"）
# に揃えて英語の固定文言とする。
_SYSTEM_PROBLEM_DETAIL = "Internal server error"


def _build_problem_response(exc: ApplicationProblem, registry: ErrorCodeRegistry) -> JSONResponse:
    status = registry.resolve_http_status(exc)
    # マスク判定はHTTPステータス（マッピングで500以上に解決されたか）では
    # なく例外の型で行う。BusinessProblemを5xxにマッピングしても、その
    # messageは呼び出し側に向けた意図的な文言なのでそのまま返す。
    detail = _SYSTEM_PROBLEM_DETAIL if isinstance(exc, SystemProblem) else exc.message
    return JSONResponse(
        status_code=status,
        content={
            "type": "about:blank",
            "title": exc.error_code,
            "status": status,
            "detail": detail,
        },
        media_type="application/problem+json",
    )


async def handle_application_error(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI/Starletteの``exception_handlers``に登録するハンドラ。

    Example::

        app = FastAPI(
            exception_handlers={ApplicationProblem: handle_application_error},
        )

    Args:
        request: 発生元のリクエスト。
        exc: ``ApplicationProblem``（またはそのサブクラス）のインスタンス。
            Starletteの``exception_handlers``の型シグネチャに合わせて
            ``Exception``として受け取るが、``exception_handlers``に
            ``ApplicationProblem``で登録する限り実際にはサブクラスのみが渡る。

    Returns:
        RFC 7807形式の``JSONResponse``。``SystemProblem``の場合、``detail``は
        内部情報の漏洩を防ぐため固定文言に置き換えられる（``title``の
        ``error_code``はそのまま返す）。

    Raises:
        TypeError: ``exc``が``ApplicationProblem``ではない場合
            （``exception_handlers``への登録キーの誤り等）。

    Note:
        ``ErrorMappingLifespanResource``（``open_error_mapping()``）を
        ``create_lifespan(...)``に登録し忘れると、このハンドラ内の
        ``get_error_mapping_resource``が``RuntimeError``を送出し、
        ``BusinessProblem``を含む**全ての**``ApplicationProblem``が
        StarletteのServerErrorMiddleware経由で素の500として返る。この
        設定漏れは起動時には検出されず、実際に例外が発生して初めて
        顕在化する。独自マッピングが不要な場合でも、空マッピング
        （``open_error_mapping()``を引数なし）で必ず登録すること。
    """
    if not isinstance(exc, ApplicationProblem):
        raise TypeError(
            "handle_application_error expects an ApplicationProblem instance, "
            f"got {type(exc).__name__}. Register it as "
            "exception_handlers={ApplicationProblem: handle_application_error}."
        )
    resource = get_error_mapping_resource(request.app)
    return _build_problem_response(exc, resource.registry)


class ErrorHandlingMiddleware:
    """ユーザー定義ミドルウェア由来の``ApplicationProblem``を拾う安全網ASGIミドルウェア。

    StarletteのExceptionMiddlewareはルーター内で解決するため、ルート
    ハンドラ由来の例外は``exception_handlers``が処理し、ここには伝播
    しない。認証・レート制限等、ルーターより外側のユーザー定義
    ミドルウェアが送出した``ApplicationProblem``だけをここで拾う。

    Starletteは最後に``add_middleware``したものが一番外側になるため、
    このミドルウェアは他の全ての``app.add_middleware(...)``呼び出しの
    **後**に登録する必要がある。

    ``SystemProblem``の``message``は内部情報の漏洩を防ぐため``detail``に
    出さず固定文言に置き換える（``handle_application_error``と同じ）。

    Note:
        ``ErrorMappingLifespanResource``（``open_error_mapping()``）を
        ``create_lifespan(...)``に登録し忘れると、このミドルウェア内の
        ``get_error_mapping_resource``が``RuntimeError``を送出し、
        ``BusinessProblem``を含む**全ての**``ApplicationProblem``が
        StarletteのServerErrorMiddleware経由で素の500として返る。この
        設定漏れは起動時には検出されず、実際に例外が発生して初めて
        顕在化する。独自マッピングが不要な場合でも、空マッピング
        （``open_error_mapping()``を引数なし）で必ず登録すること。

    Args:
        app: ラップ対象のASGIアプリケーション。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except ApplicationProblem as exc:
            if started:
                # レスポンス送信が既に始まっていれば新規レスポンスを
                # 差し替えられないため、握りつぶさず再送出する。
                raise
            resource = get_error_mapping_resource(scope["app"])
            response = _build_problem_response(exc, resource.registry)
            await response(scope, receive, send)
