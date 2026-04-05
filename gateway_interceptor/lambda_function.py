"""
AgentCore Gateway Interceptor Lambda

Forwards the client's token to the MCP target as the Authorization header.
The Gateway does not forward the Authorization header from the client; it only
forwards Authorization when provided by an interceptor lambda.

Flow:
  Agent (sends X-ID-Token) → Gateway → this Lambda → Gateway adds Authorization → MCP target
"""

import json
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Header name the agent sends (allowlist this on the gateway so the lambda receives it)
TOKEN_HEADER = "X-ID-Token"


def lambda_handler(event, context):
    """
    Interceptor entrypoint. Return transformedGatewayRequest with Authorization
    so the Gateway forwards it to the MCP target.
    """
    try:
        logger.info("Interceptor invoked. Full event: %s", json.dumps(event, default=str))

        mcp = event.get("mcp", {})
        gateway_request = mcp.get("gatewayRequest", {})
        headers = gateway_request.get("headers", {}) or {}

        logger.info("Headers received: %s", json.dumps(headers, default=str))

        # Token from client (agent sends X-ID-Token; gateway must pass it to lambda)
        token = (
            headers.get(TOKEN_HEADER)
            or headers.get("x-id-token")  # case-insensitive fallback
        )

        if token:
            logger.info("Token found (first 20 chars): %.20s...", token.strip())
        else:
            logger.warning("Token is None or empty")

        if not token or not token.strip():
            logger.warning("No token in request headers; MCP target may return 401")
            # Still forward the request so the target can return 401 with WWW-Authenticate
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": {},
                        "body": gateway_request.get("body"),
                    }
                },
            }

        # Authorization from interceptor is forwarded to the target by the Gateway
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayRequest": {
                    "headers": {
                        "Authorization": f"Bearer {token.strip()}",
                    },
                    "body": gateway_request.get("body"),
                }
            },
        }
    except Exception as e:
        logger.exception("Interceptor error: %s", e)
        raise
