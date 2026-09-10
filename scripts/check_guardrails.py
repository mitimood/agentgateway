#!/usr/bin/env python3
"""Demonstrate real allow/block/redact decisions through the authenticated gateway."""

import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

import check_governance as g
from check_telemetry import TRACE_API, span_id_hex
from trace_context import demo_trace, grafana_explore_url


ROOT = Path(__file__).resolve().parents[1]


def session(user):
    token = g.get_token(user, user)
    status, response, sid = g.mcp_post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "guardrails-demo", "version": "1"}}}, token)
    g.result_or_fail(response)
    if status != 200 or not sid:
        raise RuntimeError("No authenticated MCP session")
    g.mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"}, token, sid)
    return token, sid


def call(credentials, name, arguments, *, timeout=10):
    try:
        return g.mcp_post({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}}, *credentials, timeout=timeout)[1]
    except g.McpHTTPError as error:
        if not error.response:
            raise
        return error.response


def allowed(response):
    result = g.result_or_fail(response)
    if result.get("isError"):
        raise RuntimeError("Expected a successful tool result")
    return result


def denied(response, rule):
    error = (response or {}).get("error", {})
    if error.get("code") != -32001 or f"Guardrail [{rule}]" not in error.get("message", ""):
        raise RuntimeError(f"Expected guardrail denial: {rule}")
    print(f"BLOCK  {error['message']}", flush=True)


def verify_trace(trace_id, parent_id):
    """Check fresh gateway spans, including positive controls for upstream tracing."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(Request(f"{TRACE_API}/{trace_id}", headers={"Accept": "application/json"}), timeout=3) as response:
                trace = json.load(response)
        except OSError:
            time.sleep(1)
            continue
        spans = [span for batch in trace.get("batches", trace.get("resourceSpans", []))
                 for scope in batch.get("scopeSpans", []) for span in scope.get("spans", [])]
        tags = {s["spanId"]: {a["key"]: next(iter(a["value"].values()))
                              for a in s.get("attributes", [])} for s in spans}
        calls = [s for s in spans if s.get("kind") in (2, "SPAN_KIND_SERVER")
                 and tags[s["spanId"]].get("mcp.method.name") == "tools/call"]
        upstream = [s for s in spans if tags[s["spanId"]].get("agentgateway.outbound.subtype") == "Mcp"]
        request_checks = [s for s in spans if tags[s["spanId"]].get("http.path", "").endswith("/CheckRequest")]
        response_checks = [s for s in spans if tags[s["spanId"]].get("http.path", "").endswith("/CheckResponse")]
        if (len(calls), len(upstream), len(request_checks), len(response_checks)) != (9, 5, 9, 5):
            time.sleep(1)
            continue
        blocked = [s for s in calls if tags[s["spanId"]].get("access.outcome") == "guardrail_blocked"]
        if len(blocked) != 4 or sum(tags[s["spanId"]].get("access.outcome") == "ok" for s in calls) != 5:
            raise RuntimeError("Guardrail denials were not separated from successes in telemetry")
        blocked_ids = {s["spanId"] for s in blocked}
        if any(s.get("parentSpanId") in blocked_ids for s in upstream + response_checks):
            raise RuntimeError("A blocked call reached an MCP backend or response hook")
        if not all(sum(s.get("parentSpanId") == c["spanId"] for s in request_checks) == 1 for c in calls):
            raise RuntimeError("Missing request check for a gateway tool call")
        for c in calls:
            if c["spanId"] not in blocked_ids:
                if any(sum(s.get("parentSpanId") == c["spanId"] for s in group) != 1
                       for group in (upstream, response_checks)):
                    raise RuntimeError("Allowed call is missing its upstream or response check")
        if not any(span_id_hex(s["spanId"]) == parent_id for s in spans):
            time.sleep(1)
            continue
        if any("mcp.session.id" in t or "jwt.sub" in t for t in tags.values()):
            raise RuntimeError("Trace ID redaction failed")
        if any(value in json.dumps(trace) for value in ("api_key=demo-only-value", "password=demo-only-value", "demo@example.com")):
            raise RuntimeError("A raw demo payload leaked into telemetry")
        print("TRACE  4 blocked calls never reached a tool; 5 allowed calls have both policy hooks.", flush=True)
        return
    raise RuntimeError("Complete guardrail trace did not arrive within 30 seconds")


def check_outage(credentials):
    """Opt-in interruption of this local policy service, with guaranteed restart."""
    def compose(*args):
        return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout

    if not compose("ps", "--status", "running", "-q", "guardrails").strip():
        raise RuntimeError("Start guardrails before running the outage check")
    try:
        compose("stop", "guardrails")
        # The gateway's policy timeout is 10s; the client must outlive it.
        response = call(credentials, "hello_hello_world", {"message": "outage check"}, timeout=20)
        error = (response or {}).get("error", {})
        if error.get("code") != -32603 or "mcpGuardrails" not in error.get("message", ""):
            raise RuntimeError("Policy outage did not fail closed")
        print("BLOCK  Policy service offline: gateway failed closed.", flush=True)
    finally:
        compose("start", "guardrails")
        # Verify recovery before returning, even if the denial assertion failed.
        deadline = time.monotonic() + 30
        while True:
            try:
                allowed(call(credentials, "hello_hello_world", {"message": "recovered"}))
                break
            except (RuntimeError, KeyError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Guardrails restarted but gateway calls did not recover") from None
                time.sleep(1)
        print("ALLOW  Policy service restored; tool calls work again.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-outage", action="store_true",
                        help="briefly stop the local guardrails service, then restore and verify it")
    parser.add_argument("--verify-traces", action="store_true",
                        help="verify fresh spans prove that blocked calls never reached a tool")
    args = parser.parse_args()
    g.wait_for_keycloak()
    alice, bob = session("alice"), session("bob")
    with demo_trace("guardrails allow block redact demo") as (trace_id, parent_id):
        hello = allowed(call(alice, "hello_hello_world", {"message": "demo ready"}))
        if "demo ready" not in json.dumps(hello):
            raise RuntimeError("Hello result changed unexpectedly")
        print("ALLOW  Alice: normal Hello message.", flush=True)
        result = allowed(call(bob, "math_add_numbers", {"a": 2, "b": 3}))
        if not any(item.get("text", "").endswith(": 5") for item in result.get("content", [])):
            raise RuntimeError("Expected Math result 5")
        print("ALLOW  Bob: 2 + 3 = 5.", flush=True)
        allowed(call(alice, "time_utc_time", {}))
        denied(call(alice, "hello_hello_world", {"message": "api_key=demo-only-value"}), "secret_input")
        # Admin access does not bypass content checks.
        denied(call(bob, "hello_hello_world", {"message": "password=demo-only-value"}), "secret_input")
        denied(call(alice, "hello_hello_world", {"message": "x" * 281}), "message_length")
        denied(call(bob, "math_add_numbers", {"a": 1001, "b": 3}), "number_range")
        result = allowed(call(alice, "hello_hello_world", {"message": "Contact demo@example.com"}))
        encoded = json.dumps(result)
        if "demo@example.com" in encoded or "[REDACTED_EMAIL]" not in encoded:
            raise RuntimeError("Email redaction failed in the client-visible result")
        print("REDACT Contact [REDACTED_EMAIL] (checked the complete result).", flush=True)
        allowed(call(alice, "hello_hello_world", {"message": "same session still works"}))
    print(f"Trace: {grafana_explore_url(trace_id)}", flush=True)
    if args.verify_traces:
        verify_trace(trace_id, parent_id)
    if args.check_outage:
        check_outage(alice)
    print("OK: live guardrail checks passed.", flush=True)


if __name__ == "__main__":
    main()
