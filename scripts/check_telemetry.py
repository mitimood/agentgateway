#!/usr/bin/env python3
"""Exercise real MCP calls and verify this run's traces through Grafana's trace data source (no dependencies)."""
import json
import os
import base64
from http.client import HTTPException
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import check_governance as governance
from trace_context import demo_trace, grafana_explore_url

GRAFANA_URL = os.getenv("GRAFANA_URL", "http://127.0.0.1:3001")
TRACE_API = GRAFANA_URL + "/api/datasources/proxy/uid/tempo/api/traces"

def span_id_hex(value):
    # Tempo protobuf JSON encodes IDs as base64; accept OTLP hex too.
    if len(value) == 16 and all(c in "0123456789abcdef" for c in value.lower()):
        return value.lower()
    return base64.b64decode(value).hex()


def wait_for_trace(trace_id, parent_id, expected_target, expected_statuses):
    deadline = time.monotonic() + 30
    last = "no matching spans"
    while time.monotonic() < deadline:
        try:
            with urlopen(Request(f"{TRACE_API}/{trace_id}", headers={"Accept": "application/json"}), timeout=3) as response:
                trace = json.load(response)
            spans = [span for batch in trace.get("batches", trace.get("resourceSpans", []))
                     for scope in batch.get("scopeSpans", []) for span in scope.get("spans", [])]
            tags = [{t["key"]: next(iter(t["value"].values())) for t in s.get("attributes", [])} for s in spans]
            statuses = {int(t.get("http.status", 0)) for t in tags if t.get("mcp.method.name") == "tools/call"}
            if expected_statuses <= statuses and any(t.get("mcp.target") == expected_target for t in tags) and any(span_id_hex(s.get("spanId", "")) == parent_id for s in spans):
                if not any(span_id_hex(s.get("parentSpanId", "")) == parent_id for s in spans):
                    raise RuntimeError("client parent span reference was lost")
                if any("mcp.session.id" in t or "jwt.sub" in t for t in tags):
                    raise RuntimeError("collector privacy processor did not remove session/subject IDs")
                print(f"OK: {expected_target} call, statuses {sorted(expected_statuses)}, client context and redaction")
                print(f"Trace: {grafana_explore_url(trace_id)}")
                return
            last = f"observed tool call statuses: {statuses}"
        except (HTTPError, URLError, HTTPException, TimeoutError, ConnectionError) as error:
            last = str(error)
        time.sleep(1)
    raise RuntimeError(f"trace {trace_id} did not arrive within 30s: {last}")


def main():
    previous = os.environ.get("TRACEPARENT")
    try:
        governance.wait_for_keycloak()
        for user, tools, target, statuses in [
            ("alice", {"hello_world", "utc_time"}, "hello", {200, 400}),
            ("bob", {"hello_world", "add_numbers", "utc_time"}, "math", {200}),
        ]:
            with demo_trace(f"{user} governance workflow") as (trace_id, parent_id):
                governance.exercise_user(user, user, tools)
            wait_for_trace(trace_id, parent_id, target, statuses)
        return 0
    except (RuntimeError, KeyError, StopIteration, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        if previous is None:
            os.environ.pop("TRACEPARENT", None)
        else:
            os.environ["TRACEPARENT"] = previous


if __name__ == "__main__":
    raise SystemExit(main())
