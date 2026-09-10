#!/usr/bin/env python3
"""Smoke-test the three MCP tools through the agentgateway endpoint."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:3000/mcp")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")


def _decode(body: bytes, content_type: str) -> dict | None:
    if not body:
        return None
    if "text/event-stream" not in content_type:
        return json.loads(body)
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data:") and line[5:].strip():
            return json.loads(line[5:].strip())
    return None


def post(payload: dict, session_id: str | None = None) -> tuple[int, dict | None, str | None]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    request = Request(BASE_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read()
            return response.status, _decode(body, response.headers.get("Content-Type", "")), response.headers.get(
                "Mcp-Session-Id"
            )
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{error.code} from {BASE_URL}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"cannot reach {BASE_URL}: {error.reason}") from error


def result_or_fail(response: dict | None) -> dict:
    if not response:
        raise RuntimeError("gateway returned no JSON-RPC response")
    if "error" in response:
        raise RuntimeError(f"MCP error: {response['error']}")
    return response["result"]


def main() -> int:
    try:
        status, initialize, session_id = post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "compose-smoke-test", "version": "1.0.0"},
                },
            }
        )
        if status != 200 or not session_id:
            raise RuntimeError(f"initialize did not create a gateway session (HTTP {status})")
        post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)

        tools = result_or_fail(post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session_id)[1])["tools"]
        tool_names = [tool["name"] for tool in tools]
        expected = {
            "hello_world": {"message": "through agentgateway"},
            "add_numbers": {"a": 2, "b": 3},
            "utc_time": {},
        }
        selected: dict[str, str] = {}
        for base_name in expected:
            matches = [name for name in tool_names if name == base_name or name.endswith(base_name)]
            if len(matches) != 1:
                raise RuntimeError(f"expected exactly one federated {base_name} tool; got {matches}")
            selected[base_name] = matches[0]

        for base_name, arguments in expected.items():
            response = result_or_fail(
                post(
                    {
                        "jsonrpc": "2.0",
                        "id": base_name,
                        "method": "tools/call",
                        "params": {"name": selected[base_name], "arguments": arguments},
                    },
                    session_id,
                )[1]
            )
            text = " ".join(item.get("text", "") for item in response.get("content", []))
            print(f"{selected[base_name]} -> {text}")

        print(f"OK: agentgateway federated {len(tool_names)} tools from three MCP servers at {BASE_URL}")
        return 0
    except (RuntimeError, KeyError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
