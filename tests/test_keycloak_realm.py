import json
import unittest
from pathlib import Path


REALM_FILE = Path(__file__).parents[1] / "keycloak" / "mcp-realm.json"


class KeycloakRealmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with REALM_FILE.open() as file:
            cls.realm = json.load(file)

    def test_demo_realm_has_two_users_with_expected_roles(self) -> None:
        users = {user["username"]: user for user in self.realm["users"]}
        self.assertEqual(set(users), {"alice", "bob"})
        self.assertEqual(users["alice"]["realmRoles"], ["mcp-user"])
        self.assertEqual(users["bob"]["realmRoles"], ["mcp-user", "mcp-admin"])

    def test_agentgateway_client_is_public_and_adds_audience(self) -> None:
        client = next(client for client in self.realm["clients"] if client["clientId"] == "agentgateway")
        self.assertTrue(client["publicClient"])
        self.assertTrue(client["standardFlowEnabled"])
        self.assertTrue(client["directAccessGrantsEnabled"])
        mapper = next(mapper for mapper in client["protocolMappers"] if mapper["name"] == "agentgateway-audience")
        self.assertEqual(mapper["config"]["included.client.audience"], "agentgateway")


if __name__ == "__main__":
    unittest.main()
