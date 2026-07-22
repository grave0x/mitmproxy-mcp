import asyncio

from mitmproxy_mcp.server import main as async_main


def main() -> None:
    asyncio.run(async_main())
