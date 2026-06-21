"""fastmcp-toolkitを使用したシンプルなFastMCPサーバーの例。"""

import asyncio

from fastmcp_toolkit.decorator import span
from fastmcp_toolkit.telemetry import setup_telemetry

setup_telemetry(service_name="example-server")

from fastmcp import FastMCP  # noqa: E402

from fastmcp_toolkit import run_server  # noqa: E402
from fastmcp_toolkit.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

app = FastMCP("example-server")


async def check_database() -> tuple[bool, str]:
    """データベース接続を確認する（例示用のスタブ）。"""
    return True, "connected"


@app.tool()
def hello(name: str) -> str:
    """挨拶を返す。"""
    logger.info("tool invoked", name=name)
    return f"Hello, {name}!"


@app.tool()
def invisible():
    """ミドルウェアで非表示のツール。"""
    logger.info("unvisible tool invoked")
    return "This is a secret message."


@app.tool()
async def test_trace_span():
    """トレーススパンをテストするツール。"""
    logger.info("trace span test tool invoked")
    await inner_execution(1)
    await inner_execution(2)
    return "This is a trace span test message."


@span
async def inner_execution(count: int):
    await asyncio.sleep(0.01 * count)
    logger.info(f"inner execution {count}")


if __name__ == "__main__":
    run_server(
        app,
        json_output=False,
        readiness_checks=[check_database],
        health_check=True,
        metrics_enabled=True,
    )
