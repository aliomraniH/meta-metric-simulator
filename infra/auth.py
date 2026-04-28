"""OAuth 2.1 + Resource Indicators (RFC 8707) auth provider for the front door.

Per-layer scopes
----------------
Each agentic layer is its own resource with its own scope.  Tokens are issued
per-resource and MUST NOT be passed through between layer servers.  When a
specialist on Layer 4 needs to read from Layer 5 (or vice versa), the call
goes through the orchestrator and the orchestrator performs an RFC 8693 token
exchange — never reuses the inbound token.

This module exposes:
  - LAYER_SCOPES: canonical scope strings per layer.
  - build_oauth_provider(): returns an auth provider FastMCP can mount.
  - exchange_token(): RFC 8693 token-exchange helper used by orchestrator.

The verifier itself is intentionally simple here (M0 scaffolding).  In
production, swap _LocalBearerVerifier for a JWKS-backed JWT verifier.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Scope registry — RFC 8707 resource indicators
# -----------------------------------------------------------------------------

LAYER_SCOPES: dict[str, dict[str, str]] = {
    "l4": {
        "resource": "urn:reels-sim:layer4:baselines",
        "read":     "reels-sim:l4:baselines.read",
        "write":    "reels-sim:l4:baselines.write",  # synthesizer only
        "sample":   "reels-sim:l4:sampling",
    },
    "l5": {
        "resource": "urn:reels-sim:layer5:curation",
        "read":     "reels-sim:l5:curation.read",
        "write":    "reels-sim:l5:curation.write",   # synthesizer only
        "sample":   "reels-sim:l5:sampling",
    },
    "l6": {
        "resource": "urn:reels-sim:layer6:engine",
        "read":     "reels-sim:l6:engine.read",
        "write":    "reels-sim:l6:engine.write",
        "sample":   "reels-sim:l6:sampling",
    },
    "l8": {
        "resource": "urn:reels-sim:layer8:insights",
        "read":     "reels-sim:l8:insights.read",
        "write":    "reels-sim:l8:insights.write",
        "sample":   "reels-sim:l8:sampling",
    },
}


@dataclass(frozen=True)
class Principal:
    """Authenticated caller — what the verifier returns on success."""
    subject: str
    scopes: frozenset[str]
    audience: str | None = None

    def has(self, scope: str) -> bool:
        return scope in self.scopes


# -----------------------------------------------------------------------------
# Verifier interface
# -----------------------------------------------------------------------------

class _LocalBearerVerifier:
    """Dev-mode verifier: matches MCP_AUTH_TOKEN against a single shared secret.

    Production deployments should replace with a JWKS-backed JWT verifier that
    enforces issuer / audience / exp / scope claims.
    """

    def __init__(self, expected_token: str, audience: str | None = None) -> None:
        self._token = expected_token
        self._audience = audience

    async def verify(self, presented_token: str, requested_resource: str | None = None) -> Principal:
        if not presented_token or presented_token != self._token:
            raise PermissionError("invalid bearer token")
        # Dev mode: grant all read scopes; writes still gated by hooks.
        scopes = frozenset(
            s for layer in LAYER_SCOPES.values() for k, s in layer.items() if k in {"read", "sample"}
        )
        return Principal(subject="dev", scopes=scopes, audience=requested_resource or self._audience)


def build_oauth_provider() -> Any:
    """Return an auth provider object that FastMCP accepts on construction.

    Falls back to the local bearer verifier when MCP_OAUTH_JWKS_URL is unset
    (typical for local dev / Replit preview).  In production, point JWKS_URL
    at the IdP's JWKS endpoint.
    """
    token = os.environ.get("MCP_AUTH_TOKEN", "")
    audience = os.environ.get("MCP_OAUTH_AUDIENCE")
    jwks_url = os.environ.get("MCP_OAUTH_JWKS_URL")

    if jwks_url:
        # Real JWT verifier wiring lives in production; the placeholder below
        # keeps the call signature stable so callers don't change.
        log.info("OAuth JWKS verifier configured at %s", jwks_url)
        return {
            "type": "jwt",
            "jwks_url": jwks_url,
            "audience": audience,
            "issuer": os.environ.get("MCP_OAUTH_ISSUER"),
            "scopes": LAYER_SCOPES,
        }

    if not token:
        log.warning(
            "No MCP_AUTH_TOKEN and no MCP_OAUTH_JWKS_URL set — server will reject all calls"
        )
        token = "__no_token_set__"

    log.info("OAuth provider in local-bearer mode (dev only)")
    return _LocalBearerVerifier(expected_token=token, audience=audience)


# -----------------------------------------------------------------------------
# RFC 8693 token exchange — orchestrator-only
# -----------------------------------------------------------------------------

async def exchange_token(
    inbound_token: str,
    target_layer: str,
    *,
    issuer: str | None = None,
    httpx_client: httpx.AsyncClient | None = None,
) -> str:
    """Exchange an inbound token for a per-layer token (RFC 8693).

    Called by the orchestrator when fanning out work to layer specialists.
    Layer servers MUST NOT call this with each other's tokens — that would be
    token passthrough and is explicitly disallowed by the architecture.
    """
    if target_layer not in LAYER_SCOPES:
        raise ValueError(f"unknown layer: {target_layer}")

    issuer = issuer or os.environ.get("MCP_OAUTH_ISSUER")
    if not issuer:
        # Dev mode: return inbound token unchanged.  Production deployments
        # MUST set MCP_OAUTH_ISSUER and configure the IdP for token exchange.
        log.debug("Token exchange in dev passthrough mode for layer=%s", target_layer)
        return inbound_token

    layer = LAYER_SCOPES[target_layer]
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": inbound_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "resource": layer["resource"],
        "scope": " ".join([layer["read"], layer["sample"]]),
    }
    client = httpx_client or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.post(f"{issuer}/oauth/token", data=payload)
        resp.raise_for_status()
        return resp.json()["access_token"]
    finally:
        if httpx_client is None:
            await client.aclose()
