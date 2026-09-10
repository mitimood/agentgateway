"""Policy boundaries and the actual ExtMCP gRPC wire contract."""

import json
import unittest

import grpc

from guardrails.policy import Denied, check_request, redact_response
from guardrails.proto import ext_mcp_pb2 as pb
from guardrails.proto import ext_mcp_pb2_grpc as rpc
from guardrails.server import create_server


def request(target, name, arguments):
    return pb.McpRequest(service_names=[target], method="tools/call",
                         mcp_request=json.dumps({"name": name, "arguments": arguments}).encode())


class PolicyTests(unittest.TestCase):
    def check(self, target, name, arguments):
        req = request(target, name, arguments)
        check_request(list(req.service_names), req.mcp_request)

    def test_defaults_boundaries_and_ordinary_text_pass(self):
        for message in ("hello", "my password needs changing", "x" * 280, "Contact demo@example.com"):
            self.check("hello", "hello_world", {"message": message})
        self.check("hello", "hello_world", {})
        self.check("math", "add_numbers", {"a": -1000, "b": 1000})
        self.check("math", "add_numbers", {"a": 1.5, "b": 2})
        self.check("time", "utc_time", {})

    def test_secret_shapes_are_denied_without_echoing_payload(self):
        for message in ("password=demo-only", "API_KEY: demo-only", "secret = demo-only",
                        "sk-demo12345678901234567890", "access-token=demo-only",
                        "-----BEGIN PRIVATE KEY-----", "ＰＡＳＳＷＯＲＤ=demo-only"):
            with self.subTest(message=message), self.assertRaises(Denied) as raised:
                self.check("hello", "hello_world", {"message": message})
            self.assertEqual(raised.exception.rule, "secret_input")
            self.assertNotIn(message, str(raised.exception))

    def test_limits_and_invalid_argument_shapes_are_denied(self):
        cases = [
            ("hello", "hello_world", {"message": "x" * 281}),
            ("hello", "hello_world", {"message": 123}),
            ("hello", "hello_world", {"unexpected": "value"}),
            ("math", "add_numbers", {"a": 1001, "b": 2}),
            ("math", "add_numbers", {"a": -1001, "b": 2}),
            ("math", "add_numbers", {"a": "2", "b": 3}),
            ("math", "add_numbers", {"a": True, "b": 3}),
            ("math", "add_numbers", {"a": float("nan"), "b": 3}),
            ("math", "add_numbers", {"a": float("inf"), "b": 3}),
            ("math", "add_numbers", {"a": 2}),
            ("time", "utc_time", {"message": "extra"}),
            ("hello", "add_numbers", {"a": 2, "b": 3}),
            ("new-server", "new-tool", {}),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(Denied):
                self.check(*case)

    def test_invalid_json_and_excessive_payloads_are_denied(self):
        for raw in (b"not-json", b"[]", b"null", b"\xff", b"x" * 8193,
                    b'{"name":"hello_world","arguments":null}'):
            with self.subTest(raw=raw[:30]), self.assertRaises(Denied):
                check_request(["hello"], raw)
        with self.assertRaises(Denied):
            redact_response(b"x" * 65537)
        for targets in ([], ["unknown"], ["hello", "math"]):
            with self.subTest(targets=targets), self.assertRaises(Denied):
                check_request(targets, b"{}")

    def test_redaction_covers_text_structured_results_and_error_text(self):
        result = {"content": [{"type": "text", "text": "Email Alice+demo@example.com"}],
                  "structuredContent": {"result": "Alice+demo@example.com", "nested": [
                      {"email": "bob@example.org", "count": 2, "ok": True}]}, "isError": True}
        clean = redact_response(json.dumps(result).encode())
        self.assertNotIn(b"@example", clean)
        decoded = json.loads(clean)
        self.assertEqual(decoded["structuredContent"]["nested"][0],
                         {"email": "[REDACTED_EMAIL]", "count": 2, "ok": True})
        self.assertTrue(decoded["isError"])
        self.assertEqual(decoded["content"][0]["text"], "Email [REDACTED_EMAIL]")
        self.assertIsNone(redact_response(b'{"content": [], "isError": false}'))


class GrpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server()
        cls.port = cls.server.add_insecure_port("127.0.0.1:0")
        cls.server.start()
        cls.channel = grpc.insecure_channel(f"127.0.0.1:{cls.port}")
        cls.stub = rpc.ExtMcpStub(cls.channel)

    @classmethod
    def tearDownClass(cls):
        cls.channel.close()
        cls.server.stop(0).wait()

    def test_pass_deny_and_mutate_over_grpc_without_payload_logging(self):
        with self.assertLogs("demo.guardrails", level="INFO") as logs:
            allowed = self.stub.CheckRequest(request("math", "add_numbers", {"a": 2, "b": 3}), timeout=2)
            denied = self.stub.CheckRequest(request("hello", "hello_world", {"message": "password=demo-only"}), timeout=2)
            response = self.stub.CheckResponse(pb.McpResponse(service_names=["hello"], method="tools/call",
                mcp_response=b'{"content":[{"type":"text","text":"demo@example.com"}]}'), timeout=2)
        self.assertEqual(allowed.WhichOneof("result"), "pass")
        self.assertEqual(denied.error.code, pb.AuthorizationError.PERMISSION_DENIED)
        self.assertIn("secret_input", denied.error.reason)
        self.assertEqual(response.WhichOneof("result"), "mutated")
        self.assertIn(b"[REDACTED_EMAIL]", response.mutated)
        self.assertNotIn("demo-only", " ".join(logs.output))
        self.assertNotIn("demo@example.com", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
