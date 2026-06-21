"""fastmcp_toolkit.loggingモジュールのテスト。"""

from fastmcp_toolkit.logging import get_logger, setup_logging


def test_setup_logging_console():
    setup_logging(json_output=False)
    logger = get_logger("test")
    assert logger is not None


def test_setup_logging_json():
    setup_logging(json_output=True)
    logger = get_logger("test")
    assert logger is not None
