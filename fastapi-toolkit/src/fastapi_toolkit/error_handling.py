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


def _build_problem_response(exc: ApplicationProblem, registry: ErrorCodeRegistry) -> JSONResponse:
    status = registry.resolve_http_status(exc)
    return JSONResponse(
        status_code=status,
        content={
            "type": "about:blank",
            "title": exc.error_code,
            "status": status,
            "detail": exc.message,
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
        RFC 7807形式の``JSONResponse``。
    """
    assert isinstance(exc, ApplicationProblem)
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
