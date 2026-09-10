import unittest

from fastmcp import Client
from fastmcp.server.auth.providers.jwt import RSAKeyPair

from mcp_servers.server import create_server


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = RSAKeyPair.generate()

    def server(self, name):
        return create_server(name, public_key=self.key.public_key)

    async def test_each_server_exposes_one_distinct_tool(self) -> None:
        expected = {
            "hello": "hello_world",
            "math": "add_numbers",
            "time": "utc_time",
        }

        for server_name, tool_name in expected.items():
            with self.subTest(server=server_name):
                async with Client(self.server(server_name)) as client:
                    tools = await client.list_tools()
                self.assertEqual([tool.name for tool in tools], [tool_name])

    async def test_hello_tool_uses_default_message(self) -> None:
        async with Client(self.server("hello")) as client:
            result = await client.call_tool("hello_world", {})

        self.assertEqual(result.data, "Hello from the hello MCP server: hello")
        self.assertFalse(result.is_error)

    async def test_math_tool_call_returns_result(self) -> None:
        async with Client(self.server("math")) as client:
            result = await client.call_tool("add_numbers", {"a": 2, "b": 3})

        self.assertEqual(result.data, "math result from the math MCP server: 5")
        self.assertFalse(result.is_error)

    def test_unknown_server_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "MCP_SERVER_NAME must be one of"):
            create_server("unknown")


if __name__ == "__main__":
    unittest.main()
