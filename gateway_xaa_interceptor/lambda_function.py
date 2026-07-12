"""
AgentCore Gateway XAA Interceptor Lambda (multi-agent, Secrets Manager)

Performs Okta Cross-App Access (XAA) token exchange inside the interceptor.
Supports multiple agents calling the same Gateway by reading the X-Agent-ID
header and fetching the matching XAA config from AWS Secrets Manager.

Each agent's XAA credentials (okta_domain, principal_id, authorization_server_id,
scope, private_jwk) are stored as a separate secret in Secrets Manager under a
common prefix. The Lambda derives the secret name from X-Agent-ID:

    <prefix>/<agent_id>   e.g.  agentcore/xaa/hr-agent

Flow:
  Agent (sends X-ID-Token + X-Agent-ID)
    → Gateway
      → this Lambda
        1. Read X-Agent-ID header
        2. Fetch XAA config from Secrets Manager: <prefix>/<agent_id>
        3. XAA exchange: id_token → custom AS access_token
      → Gateway adds Authorization: Bearer <access_token>
        → MCP target

Environment variables:
  XAA_SECRET_PREFIX  – Secrets Manager prefix (default: "agentcore/xaa")
                       Secret name = <prefix>/<agent_id>

  Each secret's value is a JSON object:
    {
      "okta_domain":              "https://dev-12345.okta.com",
      "principal_id":             "0oa...",
      "authorization_server_id":  "aus...",
      "scope":                    "mcp:read",
      "private_jwk":              { ... JWK object ... }
    }

  Single-agent fallback (no X-Agent-ID or Secrets Manager needed):
    XAA_OKTA_DOMAIN, XAA_PRINCIPAL_ID, XAA_AUTHORIZATION_SERVER_ID,
    XAA_SCOPE, XAA_PRIVATE_JWK
"""

import asyncio
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TOKEN_HEADER = "X-ID-Token"
AGENT_ID_HEADER = "X-Agent-ID"
XAA_SECRET_PREFIX = os.environ.get("XAA_SECRET_PREFIX", "agentcore/xaa").strip().rstrip("/")


# ---------------------------------------------------------------------------
# Secrets Manager client + in-memory cache
# ---------------------------------------------------------------------------

_sm_client = boto3.client("secretsmanager")
_secret_cache = {}


def _fetch_agent_config_from_secrets(agent_id):
    """
    Fetch and cache XAA config for agent_id from Secrets Manager.
    Secret name: <XAA_SECRET_PREFIX>/<agent_id>
    Cached for the lifetime of the Lambda execution environment (warm starts reuse cache).
    """
    if agent_id in _secret_cache:
        logger.info("Secret cache hit for agent_id=%s", agent_id)
        return _secret_cache[agent_id]

    secret_name = f"{XAA_SECRET_PREFIX}/{agent_id}"
    logger.info("Fetching secret: %s", secret_name)
    try:
        response = _sm_client.get_secret_value(SecretId=secret_name)
        secret_str = response["SecretString"]
        config = json.loads(secret_str)
        if not isinstance(config, dict):
            logger.error("Secret %s is not a JSON object", secret_name)
            return None
        required = ["okta_domain", "principal_id", "authorization_server_id", "private_jwk"]
        missing = [k for k in required if not config.get(k)]
        if missing:
            logger.error("Secret %s missing required keys: %s", secret_name, missing)
            return None
        if isinstance(config["private_jwk"], str):
            config["private_jwk"] = json.loads(config["private_jwk"])
        _secret_cache[agent_id] = config
        logger.info("Secret loaded and cached for agent_id=%s", agent_id)
        return config
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            logger.warning("Secret %s not found in Secrets Manager", secret_name)
        elif code == "AccessDeniedException":
            logger.error("Lambda role lacks secretsmanager:GetSecretValue for %s", secret_name)
        else:
            logger.exception("Secrets Manager error for %s: %s", secret_name, e)
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.exception("Failed to parse secret %s: %s", secret_name, e)
        return None


# ---------------------------------------------------------------------------
# Single-agent fallback (flat env vars, no Secrets Manager)
# ---------------------------------------------------------------------------

def _load_single_agent_config():
    domain = os.environ.get("XAA_OKTA_DOMAIN", "").strip()
    principal = os.environ.get("XAA_PRINCIPAL_ID", "").strip()
    as_id = os.environ.get("XAA_AUTHORIZATION_SERVER_ID", "").strip()
    scope = os.environ.get("XAA_SCOPE", "mcp:read").strip() or "mcp:read"
    jwk_raw = os.environ.get("XAA_PRIVATE_JWK", "").strip()
    if not all([domain, principal, as_id, jwk_raw]):
        return None
    try:
        jwk = json.loads(jwk_raw)
    except json.JSONDecodeError:
        logger.warning("XAA_PRIVATE_JWK is not valid JSON")
        return None
    return {
        "okta_domain": domain,
        "principal_id": principal,
        "authorization_server_id": as_id,
        "scope": scope,
        "private_jwk": jwk,
    }


_SINGLE_AGENT_CONFIG = _load_single_agent_config()


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def _resolve_config(agent_id):
    """
    Resolve XAA config for this request.
    Priority:
      1. X-Agent-ID present → fetch from Secrets Manager (<prefix>/<agent_id>)
      2. No X-Agent-ID → single-agent fallback (flat env vars)
    """
    if agent_id:
        config = _fetch_agent_config_from_secrets(agent_id)
        if config:
            return config
        logger.warning("Could not load secret for agent_id=%s; trying single-agent fallback", agent_id)

    if _SINGLE_AGENT_CONFIG:
        logger.info("Using single-agent fallback config (env vars)")
        return _SINGLE_AGENT_CONFIG

    return None


# ---------------------------------------------------------------------------
# XAA exchange
# ---------------------------------------------------------------------------

async def _exchange_id_token_via_xaa(id_token, config):
    """
    Cross-App Access (ID-JAG) using okta-client-python.
    id_token → flow.start() (ID-JAG) → flow.resume() → custom AS access_token.
    """
    from okta_client.authfoundation import (
        OAuth2Client,
        OAuth2ClientConfiguration,
        LocalKeyProvider,
    )
    from okta_client.authfoundation.oauth2.jwt_bearer_claims import JWTBearerClaims
    from okta_client.authfoundation.oauth2.client_authorization import ClientAssertionAuthorization
    from okta_client.oauth2auth import CrossAppAccessFlow, CrossAppAccessTarget

    okta_domain = config["okta_domain"].rstrip("/")
    if not okta_domain.startswith(("http://", "https://")):
        okta_domain = "https://" + okta_domain
    jwk = config["private_jwk"]
    principal_id = config["principal_id"]
    as_id = config["authorization_server_id"]
    scope_str = config.get("scope", "mcp:read")

    jwt_audience = f"{okta_domain}/oauth2/v1/token"
    target_issuer = f"{okta_domain}/oauth2/{as_id}"
    id_jag_audience = target_issuer

    key_provider = LocalKeyProvider(
        key=jwk,
        algorithm=jwk.get("alg", "RS256"),
        key_id=jwk.get("kid"),
    )
    jwt_claims = JWTBearerClaims(
        issuer=principal_id,
        subject=principal_id,
        audience=jwt_audience,
        expires_in=300,
    )
    oauth2_config = OAuth2ClientConfiguration(
        issuer=okta_domain,
        client_authorization=ClientAssertionAuthorization(
            assertion_claims=jwt_claims,
            key_provider=key_provider,
        ),
    )
    oauth2_client = OAuth2Client(configuration=oauth2_config)
    target = CrossAppAccessTarget(issuer=target_issuer)
    flow = CrossAppAccessFlow(client=oauth2_client, target=target)

    scope_list = [s.strip() for s in scope_str.split() if s.strip()] or ["mcp:read"]
    await flow.start(
        token=id_token,
        audience=id_jag_audience,
        scope=scope_list,
        token_type="id_token",
    )

    id_jag_token_obj = flow.context.id_jag_token
    if id_jag_token_obj and id_jag_token_obj.access_token:
        logger.info("XAA ID-JAG step complete")
    else:
        logger.warning("XAA ID-JAG: no id_jag_token in flow.context")

    auth_server_result = await flow.resume()
    access_token = auth_server_result.access_token
    logger.info("XAA exchange complete — obtained custom AS access_token")
    return access_token


def _run_xaa_exchange(id_token, config):
    """Run the async XAA flow from the sync Lambda handler."""
    return asyncio.run(_exchange_id_token_via_xaa(id_token, config))


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    Interceptor entrypoint.
    1. Read X-ID-Token and X-Agent-ID from the Gateway request headers.
    2. Fetch XAA config from Secrets Manager (by agent ID) or env var fallback.
    3. Exchange id_token via XAA for a custom AS access_token.
    4. Return Authorization: Bearer <access_token>.
    """
    try:
        logger.info("XAA Interceptor invoked. Event keys: %s", list(event.keys()))

        mcp = event.get("mcp", {})
        gateway_request = mcp.get("gatewayRequest", {})
        headers = gateway_request.get("headers", {}) or {}

        logger.info("Headers received: %s", json.dumps(
            {k: (v[:20] + "..." if len(v) > 20 else v) for k, v in headers.items()},
            default=str,
        ))

        id_token = (
            headers.get(TOKEN_HEADER)
            or headers.get("x-id-token")
        )
        agent_id = (
            headers.get(AGENT_ID_HEADER)
            or headers.get("x-agent-id")
        )

        if agent_id:
            agent_id = agent_id.strip()
            logger.info("Agent ID: %s", agent_id)
        else:
            logger.info("No X-Agent-ID header; will use single-agent fallback if available")

        if not id_token or not id_token.strip():
            logger.warning("No id_token in request headers; forwarding without Authorization")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": {},
                        "body": gateway_request.get("body"),
                    }
                },
            }

        id_token = id_token.strip()
        logger.info("id_token present (first 20 chars): %.20s...", id_token)

        config = _resolve_config(agent_id)
        if not config:
            logger.error(
                "No XAA config found for agent_id=%s. Ensure secret '%s/%s' exists in "
                "Secrets Manager, or set flat env vars for single-agent fallback. "
                "Falling back to id_token passthrough.",
                agent_id, XAA_SECRET_PREFIX, agent_id,
            )
            access_token = id_token
        else:
            access_token = _run_xaa_exchange(id_token, config)
            logger.info("XAA access_token obtained (first 20 chars): %.20s...", access_token)

        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayRequest": {
                    "headers": {
                        "Authorization": f"Bearer {access_token}",
                    },
                    "body": gateway_request.get("body"),
                }
            },
        }
    except Exception as e:
        logger.exception("XAA Interceptor error: %s", e)
        raise
