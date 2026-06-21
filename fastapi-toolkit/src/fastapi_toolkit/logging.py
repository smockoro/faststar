"""structlogベースの構造化ロギング設定。"""

import logging
from functools import partial

import structlog


def _add_application_id(
    _logger: object,
    _method_name: str,
    event_dict: structlog.types.EventDict,
    *,
    _application_id: str,
) -> structlog.types.EventDict:
    if _application_id:
        event_dict["application_id"] = _application_id
    return event_dict


def setup_logging(
    *,
    application_id: str = "",
    level: int = logging.INFO,
    json_output: bool = False,
) -> None:
    """アプリケーション全体の構造化ロギングを設定する。

    structlogと標準loggingモジュールの両方を設定し、
    サードパーティライブラリ（uvicorn等）のログも構造化出力に統一する。

    Args:
        application_id: アプリケーション識別子。
            全ログ行に ``application_id`` として含まれる。
        level: 最小ログレベル。
        json_output: Trueの場合JSON形式で出力（本番用）。
            Falseの場合コンソール形式（開発用）。
    """
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        partial(_add_application_id, _application_id=application_id),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.EventRenamer("message"),
    ]
    if json_output:
        shared_processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicornは独自にハンドラを追加しpropagate=Falseにするため、
    # rootのフォーマッタが適用されない。ハンドラを除去しrootに伝播させる。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


def get_logger(name: str | None = None, *, log_type: str = "application") -> structlog.stdlib.BoundLogger:
    """構造化ロガーインスタンスを取得する。

    Args:
        name: ロガー名（通常は ``__name__``）。
        log_type: 全ログ行に含まれるログ種別識別子。

    Returns:
        設定済みのstructlog BoundLoggerインスタンス。
    """
    return structlog.get_logger(name, log_type=log_type)
