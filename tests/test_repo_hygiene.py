"""Protect local credentials while retaining the source needed by a clean clone."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneTests(unittest.TestCase):
    def test_git_excludes_secrets_and_generated_files_but_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True, capture_output=True)
            (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
            ignored = [
                ".secrets/gateway-private.pem", "copy/gateway-public.pem", "backup/signing.key",
                ".env", ".env.production", "cert.p12", "client.pfx", "id_rsa", "credentials.json",
                ".venv/bin/python", "mcp_servers/__pycache__/server.pyc", ".DS_Store",
                ".deck-build/preview.png", ".telemetry-build/traces.json", ".chart-data-demo/data.csv",
                ".codex-finalizer/check.json", "deliverables/~$briefing.pptx", "request.har",
                "deliverables/node_modules",
                "deliverables/mcp-gateway-engineering-briefing-v9.pptx",
                "deliverables/mcp-gateway-presenter-guide.md",
            ]
            retained = [".env.example", "pyproject.toml", "uv.lock", "compose.yaml",
                        "scripts/setup_backend_auth.py", "mcp_servers/auth.py",
                        "keycloak/mcp-realm.json", "observability/grafana/dashboards/gateway.json"]
            result = subprocess.run(["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "--stdin"],
                                    cwd=root, input="\n".join(ignored + retained) + "\n",
                                    text=True, capture_output=True, check=True)
            self.assertEqual(set(result.stdout.splitlines()), set(ignored))


if __name__ == "__main__":
    unittest.main()
