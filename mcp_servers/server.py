#!/usr/bin/env python3
"""Three tiny FastMCP servers selected by ``MCP_SERVER_NAME``."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.auth import GatewayTokenVerifier


SERVER_NAMES = {"hello", "math", "time"}


def create_server(server_name: str, *, public_key: str | None = None) -> FastMCP:
    """Create one demo server with exactly one tool."""

    if server_name not in SERVER_NAMES:
        choices = ", ".join(sorted(SERVER_NAMES))
        raise ValueError(f"MCP_SERVER_NAME must be one of: {choices}")

    if public_key is None:
        public_key = Path(os.getenv(
            "MCP_GATEWAY_PUBLIC_KEY_FILE", "/run/secrets/gateway-public.pem"
        )).read_text()
    mcp = FastMCP(f"demo-{server_name}", auth=GatewayTokenVerifier(public_key, server_name))

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(_request: Request) -> JSONResponse:
        return JSONResponse(
            {"status": "ok", "server": server_name, "transport": "streamable-http"}
        )

    if server_name == "hello":

        @mcp.tool
        def hello_world(message: str = "hello") -> str:
            """Return a greeting from the hello MCP server."""

            return f"Hello from the hello MCP server: {message}"

    elif server_name == "math":

        @mcp.tool
        def add_numbers(a: int | float, b: int | float) -> str:
            """Add two numbers in the math MCP server."""

            return f"math result from the math MCP server: {a + b}"

    else:

        @mcp.tool
        def utc_time() -> str:
            """Return the current UTC time from the time MCP server."""

            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            return f"UTC time from the time MCP server: {timestamp}"

    return mcp


if __name__ == "__main__":
    server_name = os.getenv("MCP_SERVER_NAME", "hello").lower()
    try:
        mcp = create_server(server_name)
    except (OSError, ValueError) as error:
        raise SystemExit(f"MCP server configuration failed: {error}") from error
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        path="/mcp",
    )
