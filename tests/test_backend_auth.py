"""Exercise authentication on the HTTP transport, not the in-memory MCP client."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastmcp.server.auth.providers.jwt import RSAKeyPair
from starlette.testclient import TestClient

from mcp_servers.server import create_server


INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "auth-test", "version": "1"}},
}
HEADERS = {"Accept": "application/json, text/event-stream"}


class BackendAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = RSAKeyPair.generate()

    def token(self, **overrides):
        options = dict(subject="agentgateway", issuer="urn:agentgateway:backend",
                       audience="urn:mcp:math", scopes=["mcp:invoke"], expires_in_seconds=60)
        options.update(overrides)
        return self.key.create_token(**options)

    def client(self, name="math"):
        server = create_server(name, public_key=self.key.public_key)
        return TestClient(server.http_app(path="/mcp", json_response=True))

    def test_missing_invalid_and_wrong_credentials_are_rejected(self):
        cases = {
            "missing": None,
            "malformed": "invalid-token",
            "expired": self.token(expires_in_seconds=-1),
            "wrong audience": self.token(audience="urn:mcp:hello"),
            "wrong issuer": self.token(issuer="untrusted"),
            "wrong identity": self.token(subject="alice"),
            "missing scope": self.token(scopes=[]),
            "missing expiry": self.token(additional_claims={"exp": None}),
            "long-lived": self.token(expires_in_seconds=3600),
            "future nbf": self.token(additional_claims={"nbf": int(time.time()) + 60}),
            "forged signature": RSAKeyPair.generate().create_token(
                subject="agentgateway", issuer="urn:agentgateway:backend",
                audience="urn:mcp:math", scopes=["mcp:invoke"], expires_in_seconds=60),
        }
        with self.client() as client:
            for name, token in cases.items():
                with self.subTest(case=name):
                    headers = dict(HEADERS)
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                    response = client.post("/mcp", json=INITIALIZE, headers=headers)
                    self.assertEqual(response.status_code, 401, name)
                    self.assertIn("Bearer", response.headers["www-authenticate"])

    def test_valid_service_tokens_work_for_each_server(self):
        for name in ("hello", "math", "time"):
            with self.subTest(server=name), self.client(name) as client:
                response = client.post("/mcp", json=INITIALIZE, headers={
                    **HEADERS, "Authorization": f"Bearer {self.token(audience=f'urn:mcp:{name}')}"})
                self.assertEqual(response.status_code, 200)

    def test_session_id_does_not_replace_auth_and_token_rotation_preserves_session(self):
        with self.client() as client:
            headers = {**HEADERS, "Authorization": f"Bearer {self.token()}"}
            response = client.post("/mcp", json=INITIALIZE, headers=headers)
            self.assertEqual(response.status_code, 200)
            session = response.headers["mcp-session-id"]
            headers["Mcp-Session-Id"] = session
            client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
            call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "add_numbers", "arguments": {"a": 2, "b": 3}}}
            denied = client.post("/mcp", json=call, headers={**HEADERS, "Mcp-Session-Id": session})
            self.assertEqual(denied.status_code, 401)
            # The gateway signs a fresh token per request, with stable service identity.
            headers["Authorization"] = f"Bearer {self.token(additional_claims={'jti': 'next-request'})}"
            allowed = client.post("/mcp", json=call, headers=headers)
            self.assertEqual(allowed.status_code, 200)
            self.assertFalse(allowed.json()["result"].get("isError", False))
            self.assertIn("5", allowed.json()["result"]["content"][0]["text"])

    def test_health_remains_available_without_auth(self):
        with self.client() as client:
            self.assertEqual(client.get("/health").status_code, 200)

    def test_missing_public_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"MCP_GATEWAY_PUBLIC_KEY_FILE": directory + "/missing.pem"}):
                with self.assertRaises(FileNotFoundError):
                    create_server("math")


if __name__ == "__main__":
    unittest.main()
