#!/usr/bin/env python3
"""Validate local tests, the running authenticated MCP stack, and Grafana telemetry."""

from pathlib import Path
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[1]
    commands = [
        ["-m", "unittest", "discover", "-s", "tests", "-v"],
        ["scripts/check_governance.py"],
        ["scripts/check_backend_auth.py"],
        ["scripts/check_guardrails.py", "--verify-traces"],
        ["scripts/check_telemetry.py"],
        ["scripts/check_dashboard.py"],
    ]
    for command in commands:
        if command == ["scripts/check_dashboard.py"]:
            # Allow earlier traces to flush and be scraped before measuring deltas.
            time.sleep(15)
        print(f"Checking: {' '.join(command)}", flush=True)
        subprocess.run([sys.executable, *command], cwd=root, check=True)
    print("OK: all local tests and live authentication, governance, guardrail, telemetry, and dashboard checks passed.")


if __name__ == "__main__":
    main()
