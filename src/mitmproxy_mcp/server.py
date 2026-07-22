from __future__ import annotations

import argparse
import json

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

from mitmproxy_mcp.flow_store import FlowStore
from mitmproxy_mcp.proxy_manager import ProxyManager

store = FlowStore()
manager = ProxyManager(store)


async def main() -> None:
    parser = argparse.ArgumentParser(description="mitmproxy MCP server")
    parser.add_argument("--proxy-port", type=int, default=8080)
    args = parser.parse_args()

    server = Server("mitmproxy-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="proxy_start",
                description="Start the mitmproxy instance",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "port": {
                            "type": "integer",
                            "description": "Listen port (default 8080)",
                        }
                    },
                },
            ),
            types.Tool(
                name="proxy_stop",
                description="Stop the mitmproxy instance",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="list_flows",
                description="List captured HTTP flows",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of flows (default 50)",
                        }
                    },
                },
            ),
            types.Tool(
                name="get_flow",
                description="Get details of a specific flow by ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "flow_id": {"type": "string"}
                    },
                    "required": ["flow_id"],
                },
            ),
            types.Tool(
                name="clear_flows",
                description="Clear all stored flows",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="set_intercept",
                description="Set an intercept pattern (empty string to clear)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Intercept pattern (e.g. ~u google)",
                        }
                    },
                    "required": ["pattern"],
                },
            ),
            types.Tool(
                name="resume_flow",
                description="Resume an intercepted flow",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "flow_id": {"type": "string"}
                    },
                    "required": ["flow_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "proxy_start":
            port = arguments.get("port", 8080)
            result = manager.start(port=port)
            return [types.TextContent(type="text", text=result)]
        elif name == "proxy_stop":
            result = manager.stop()
            return [types.TextContent(type="text", text=result)]
        elif name == "list_flows":
            max_results = arguments.get("max_results", 50)
            flows = store.list_all()[:max_results]
            return [types.TextContent(type="text", text=json.dumps(flows, indent=2))]
        elif name == "get_flow":
            flow = store.get(arguments["flow_id"])
            if flow:
                return [types.TextContent(type="text", text=json.dumps(flow, indent=2))]
            return [types.TextContent(type="text", text="flow not found")]
        elif name == "clear_flows":
            store.clear()
            return [types.TextContent(type="text", text="flows cleared")]
        elif name == "set_intercept":
            result = manager.set_intercept(arguments["pattern"])
            return [types.TextContent(type="text", text=result)]
        elif name == "resume_flow":
            result = manager.resume_flow(arguments["flow_id"])
            return [types.TextContent(type="text", text=result)]
        else:
            raise ValueError(f"unknown tool: {name}")

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="mitmproxy-mcp",
                server_version="0.1.0",
            ),
        )
