from contextlib import asynccontextmanager
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
from dotenv import load_dotenv
import httpx
import json
import logging
import os

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

HR_MCP_SERVER_URL = os.getenv("HR_MCP_SERVER_URL", "").strip()
MODEL_ID = os.getenv("MODEL_ID", "")

# XAA (Cross-App Access): exchange id_token for auth-server token when calling MCP. Uses okta-client-python.
XAA_OKTA_DOMAIN = os.getenv("XAA_OKTA_DOMAIN", "").strip()
XAA_PRINCIPAL_ID = os.getenv("XAA_PRINCIPAL_ID", "").strip()
XAA_AUTHORIZATION_SERVER_ID = os.getenv("XAA_AUTHORIZATION_SERVER_ID", "").strip()
XAA_SCOPE = os.getenv("XAA_SCOPE", "mcp:read").strip() or "mcp:read"


def _parse_xaa_private_jwk():
    raw = os.getenv("XAA_PRIVATE_JWK", "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("XAA_PRIVATE_JWK is not valid JSON; XAA disabled")
        return None


_XAA_PRIVATE_JWK = _parse_xaa_private_jwk()


def _is_xaa_configured():
    return bool(
        XAA_OKTA_DOMAIN and XAA_PRINCIPAL_ID and _XAA_PRIVATE_JWK and XAA_AUTHORIZATION_SERVER_ID
    )



async def _get_mcp_token_via_xaa_async(id_token: str) -> str:
    """
    Cross-App Access (ID-JAG) using okta-client-python. Logic aligned with
    test_package/test_cross_app_local.py (working example): flow.start() with
    token, audience, scope, token_type="id_token"; then flow.resume() for access token.
    Uses ClientAssertionAuthorization directly so flow.resume() has client_id for the
    JWT bearer token request to the custom auth server.
    """
    from okta_client.authfoundation import (
        OAuth2Client,
        OAuth2ClientConfiguration,
        LocalKeyProvider,
    )
    from okta_client.authfoundation.oauth2.jwt_bearer_claims import JWTBearerClaims
    from okta_client.authfoundation.oauth2.client_authorization import ClientAssertionAuthorization
    from okta_client.oauth2auth import CrossAppAccessFlow, CrossAppAccessTarget

    # Align with test_package/test_cross_app_local.py: okta_domain = base URL with scheme
    okta_domain = (XAA_OKTA_DOMAIN or "").strip().rstrip("/")
    if not okta_domain.startswith(("http://", "https://")):
        okta_domain = "https://" + okta_domain
    jwk = _XAA_PRIVATE_JWK

    # Same as test: JWT audience = org token endpoint; config issuer = okta_domain; target issuer = custom auth server
    jwt_audience = f"{okta_domain}/oauth2/v1/token"
    target_issuer = f"{okta_domain}/oauth2/{XAA_AUTHORIZATION_SERVER_ID}"
    id_jag_audience = target_issuer  # ID-JAG token audience = custom auth server (same as test)

    key_provider = LocalKeyProvider(
        key=jwk,
        algorithm=jwk.get("alg", "RS256"),
        key_id=jwk.get("kid"),
    )
    jwt_claims = JWTBearerClaims(
        issuer=XAA_PRINCIPAL_ID,
        subject=XAA_PRINCIPAL_ID,
        audience=jwt_audience,
        expires_in=300,
    )
    agent_sdk_config = OAuth2ClientConfiguration(
        issuer=okta_domain,
        client_authorization=ClientAssertionAuthorization(
            assertion_claims=jwt_claims,
            key_provider=key_provider,
        ),
    )
    agent_sdk = OAuth2Client(configuration=agent_sdk_config)
    target = CrossAppAccessTarget(issuer=target_issuer)
    flow = CrossAppAccessFlow(client=agent_sdk, target=target)

    scope_list = [s.strip() for s in XAA_SCOPE.split() if s.strip()] or ["mcp:read"]
    await flow.start(
        token=id_token,
        audience=id_jag_audience,
        scope=scope_list,
        token_type="id_token",
    )
    id_jag_token_obj = flow.context.id_jag_token
    if id_jag_token_obj and id_jag_token_obj.access_token:
        logger.info("XAA ID-JAG complete")
    else:
        logger.warning("XAA ID-JAG: no id_jag_token in flow.context")

    auth_server_result = await flow.resume()
    access_token = auth_server_result.access_token
    logger.info("XAA final exchange complete")
    return access_token


def get_mcp_token_via_xaa(id_token: str) -> str:
    """Run async XAA flow from sync entrypoint."""
    import asyncio
    return asyncio.run(_get_mcp_token_via_xaa_async(id_token))

# Content-Type the MCP Python SDK accepts (it does not accept application/x-ndjson)
NDJSON_CT = "application/x-ndjson"
JSON_CT = "application/json"


class _NDJSONContentTypeTransport(httpx.AsyncBaseTransport):
    """
    Wraps the real transport and rewrites responses with Content-Type application/x-ndjson
    to application/json so the MCP Python client accepts them (it only allows
    application/json or text/event-stream).
    """

    def __init__(self, transport: httpx.AsyncBaseTransport):
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._transport.handle_async_request(request)
        ct = (response.headers.get("content-type") or "").lower().split(";")[0].strip()
        if ct.startswith(NDJSON_CT):
            content = await response.aread()
            new_headers = dict(response.headers)
            new_headers["content-type"] = JSON_CT
            return httpx.Response(
                response.status_code,
                headers=new_headers,
                content=content,
                extensions=response.extensions,
            )
        return response


@asynccontextmanager
async def _transport_with_auth(mcp_url: str, token: str):
    """Streamable HTTP transport with Authorization: Bearer token. MCP is called directly (no gateway)."""
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(30.0, read=300.0)
    base = httpx.AsyncHTTPTransport()
    wrapped = _NDJSONContentTypeTransport(base)
    async with httpx.AsyncClient(
        transport=wrapped,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        async with streamable_http_client(mcp_url, http_client=client) as streams:
            yield streams


def create_streamable_http_transport(mcp_url: str, token: str):
    """Return transport that sends Authorization: Bearer <token> on all MCP requests (direct to server)."""
    return _transport_with_auth(mcp_url, token)


def _list_mcp_tools(client: MCPClient):
    """List all MCP tools with pagination."""
    tools, pagination_token = [], None
    while True:
        page = client.list_tools_sync(pagination_token=pagination_token)
        tools.extend(page)
        pagination_token = page.pagination_token
        if not pagination_token:
            break
    return tools


@tool
def get_weather():
    """Get the weather."""
    return "Weather is sunny"


model = BedrockModel(model_id=MODEL_ID)
STATIC_TOOLS = [get_weather]
DEFAULT_SYSTEM_PROMPT = (
    "You're a helpful assistant. Use the available tools to answer the user. "
    "When HR or employee data is requested, use the tools provided by the HR system."
)


def _get_mcp_bearer_token(id_token: str) -> str:
    """Resolve bearer token for MCP: XAA exchange if configured, else id_token. Raises on XAA failure."""
    if not _is_xaa_configured():
        return id_token
    try:
        return get_mcp_token_via_xaa(id_token)
    except Exception as e:
        logger.exception("XAA token exchange failed")
        if hasattr(e, "error_description"):
            logger.error("XAA OAuth2 error_description: %s", getattr(e, "error_description"))
        if hasattr(e, "details") and isinstance(getattr(e, "details"), dict):
            logger.error("XAA OAuth2 details: %s", getattr(e, "details"))
        raise


@app.entrypoint
def strands_agent_bedrock(payload, context):
    """
    Run agent: static tools only when no MCP URL; when MCP URL is set, connect to MCP (with XAA
    token if configured), merge tools, and run. MCP is only used when HR_MCP_SERVER_URL is set;
    general questions still get the full tool list so the model can choose whether to call tools.
    """
    user_input = payload.get("prompt", "").strip()
    id_token = (payload.get("id_token") or "").strip()
    if not id_token:
        return "Authentication required. No id_token provided."

    # No MCP URL: answer with static tools only (no XAA, no MCP connection).
    if not HR_MCP_SERVER_URL:
        agent = Agent(model=model, tools=STATIC_TOOLS, system_prompt=DEFAULT_SYSTEM_PROMPT)
        response = agent(user_input)
        return response.message["content"][0]["text"]

    # MCP URL set: get token (XAA if configured), connect MCP, run with static + MCP tools.
    try:
        token = _get_mcp_bearer_token(id_token)
    except Exception as e:
        return f"Cross-App Access failed: {e}"

    access_token = payload.get("access_token")
    if isinstance(access_token, dict):
        access_token = access_token.get("access_token", "")
    invocation_state = {"access_token": access_token or "", "id_token": id_token}

    try:
        mcp_client = MCPClient(lambda: create_streamable_http_transport(HR_MCP_SERVER_URL, token))
        with mcp_client:
            mcp_tools = _list_mcp_tools(mcp_client)
            all_tools = STATIC_TOOLS + mcp_tools
            agent = Agent(model=model, tools=all_tools, system_prompt=DEFAULT_SYSTEM_PROMPT)
            response = agent(user_input, invocation_state=invocation_state)
        return response.message["content"][0]["text"]
    except Exception as e:
        logger.exception("MCP or agent error")
        return f"Error: {e}"


if __name__ == "__main__":
    app.run()
