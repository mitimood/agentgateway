#!/usr/bin/env python3
"""Verify Keycloak users receive the intended MCP tools through agentgateway."""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GATEWAY_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:3000/mcp")
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://127.0.0.1:8080")
CLIENT_ID = "agentgateway"


class McpHTTPError(RuntimeError):
    """Retain a decoded MCP rejection for checks while preserving existing errors."""

    def __init__(self, status, detail, response):
        self.status = status
        self.response = response
        super().__init__(f"{status} from {GATEWAY_URL}: {detail}")


def decode_mcp(body: bytes, content_type: str) -> dict | None:
    if not body:
        return None
    if "text/event-stream" not in content_type:
        return json.loads(body)
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data:") and line[5:].strip():
            return json.loads(line[5:].strip())
    return None


def wait_for_keycloak() -> None:
    url = f"{KEYCLOAK_URL}/realms/mcp"
    last_error: Exception | None = None
    for _ in range(30):
        try:
            with urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError) as error:
            last_error = error
        time.sleep(2)
    raise RuntimeError(f"Keycloak realm did not become ready: {last_error}")


def get_token(username: str, password: str) -> str:
    data = urlencode(
        {
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": username,
            "password": password,
            "scope": "openid profile",
        }
    ).encode()
    request = Request(
        f"{KEYCLOAK_URL}/realms/mcp/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            token = json.loads(response.read()).get("access_token")
    except (HTTPError, URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not obtain a token for {username}: {error}") from error
    if not token:
        raise RuntimeError(f"Keycloak returned no access token for {username}")
    return token


def mcp_post(payload: dict, token: str, session_id: str | None = None, *, timeout: float = 10) -> tuple[int, dict | None, str | None]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if os.getenv("TRACEPARENT"):
        headers["traceparent"] = os.environ["TRACEPARENT"]
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = Request(GATEWAY_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, decode_mcp(body, response.headers.get("Content-Type", "")), response.headers.get(
                "Mcp-Session-Id"
            )
    except HTTPError as error:
        body = error.read()
        detail = body.decode("utf-8", errors="replace")
        try:
            response = decode_mcp(body, error.headers.get("Content-Type", ""))
        except (ValueError, UnicodeError):
            response = None
        raise McpHTTPError(error.code, detail, response) from error
    except URLError as error:
        raise RuntimeError(f"cannot reach {GATEWAY_URL}: {error.reason}") from error


def result_or_fail(response: dict | None) -> dict:
    if not response:
        raise RuntimeError("gateway returned no JSON-RPC response")
    if "error" in response:
        raise RuntimeError(f"MCP error: {response['error']}")
    return response["result"]


def exercise_user(username: str, password: str, expected_tool_suffixes: set[str]) -> None:
    token = get_token(username, password)
    status, initialize, session_id = mcp_post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "governance-smoke-test", "version": "1.0.0"},
            },
        },
        token,
    )
    if status != 200 or not session_id:
        raise RuntimeError(f"{username}: initialize did not create an MCP session (HTTP {status})")
    result_or_fail(initialize)
    mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"}, token, session_id)

    tools = result_or_fail(
        mcp_post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token, session_id)[1]
    )["tools"]
    names = [tool["name"] for tool in tools]
    visible_suffixes = {suffix for suffix in ("hello_world", "add_numbers", "utc_time") if any(name.endswith(suffix) for name in names)}
    if visible_suffixes != expected_tool_suffixes:
        raise RuntimeError(f"{username}: expected {sorted(expected_tool_suffixes)}, got {sorted(visible_suffixes)}")

    if username == "alice":
        tool_name = next(name for name in names if name.endswith("hello_world"))
        arguments = {"message": "permission check"}
    else:
        tool_name = next(name for name in names if name.endswith("add_numbers"))
        arguments = {"a": 2, "b": 3}
    call = result_or_fail(
        mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            token,
            session_id,
        )[1]
    )
    text = " ".join(item.get("text", "") for item in call.get("content", []))
    print(f"{username}: {', '.join(names)} -> {text}")

    if username == "alice":
        hidden_math = next((name for name in ("math_add_numbers", "add_numbers") if name in names), "math_add_numbers")
        try:
            _, denied, _ = mcp_post(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": hidden_math, "arguments": {"a": 2, "b": 3}},
                },
                token,
                session_id,
            )
        except RuntimeError as error:
            if "403" not in str(error) and not ("400" in str(error) and "Unknown tool" in str(error)):
                raise
        else:
            if not denied or "error" not in denied:
                raise RuntimeError("alice: hidden math tool was not denied")
        print("alice: direct math invocation correctly denied")


def main() -> int:
    try:
        wait_for_keycloak()
        exercise_user("alice", "alice-demo-password", {"hello_world", "utc_time"})
        exercise_user("bob", "bob-demo-password", {"hello_world", "add_numbers", "utc_time"})
        print("OK: Keycloak authentication and role-based MCP authorization are working")
        return 0
    except (RuntimeError, KeyError, StopIteration, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
