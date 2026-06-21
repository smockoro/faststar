"""共有テストフィクスチャ。"""

from functools import partial

import pytest
import structlog
import structlog.testing

from fastmcp_toolkit.logging import _add_application_id, setup_logging


@pytest.fixture
def setup_structlog():
    """テスト出力用にstructlogを設定する。"""
    setup_logging(json_output=False)


@pytest.fixture
def capture_logs():
    """structlog出力をdictのリストとしてキャプチャする。

    merge_contextvarsと_add_application_idを実行し、キャプチャされた
    エントリにsession_id、application_id等を含める。
    このフィクスチャの前にsetup_logging()を呼ぶこと。
    """
    with structlog.testing.capture_logs(
        processors=[
            structlog.contextvars.merge_contextvars,
            partial(_add_application_id, _application_id=""),
        ]
    ) as logs:
        yield logs
