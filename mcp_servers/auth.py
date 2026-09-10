"""Verify short-lived gateway credentials at the MCP HTTP boundary."""

import time

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.jwt import JWTVerifier


class GatewayTokenVerifier(JWTVerifier):
    def __init__(self, public_key: str, server_name: str):
        super().__init__(
            public_key=public_key,
            algorithm="RS256",
            issuer="urn:agentgateway:backend",
            audience=f"urn:mcp:{server_name}",
            required_scopes=["mcp:invoke"],
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = await super().load_access_token(token)
        if access is None:
            return None
        claims = access.claims
        # FastMCP checks signatures, issuer, audience, scopes and expiration.
        # This service contract also requires an expiry and gateway identity.
        if claims.get("sub") != "agentgateway" or access.expires_at is None:
            return None
        now = time.time()
        for name in ("iat", "nbf"):
            value = claims.get(name)
            if value is not None and (type(value) not in (int, float) or value > now):
                return None
        if not now < access.expires_at <= now + 65:
            return None
        return access
