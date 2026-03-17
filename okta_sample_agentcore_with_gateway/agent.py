from contextlib import asynccontextmanager
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
from dotenv import load_dotenv
import httpx
import logging
import os

# Local dev: load from .env. AgentCore runtime: env vars come from launch(env_vars=...) in agent_deployement.py
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# Config from environment (set in .env locally; in AgentCore set via agent_deployement.py env_vars)
HR_MCP_GATEWAY_URL = os.getenv("HR_MCP_GATEWAY_URL", "").strip()
MODEL_ID = os.getenv("MODEL_ID", "")


class _AuthHeaderTransport(httpx.AsyncBaseTransport):
    """Injects Authorization and X-ID-Token on every request. Gateway strips Authorization
    but can pass X-ID-Token to the interceptor lambda, which then returns Authorization."""
    def __init__(self, transport: httpx.AsyncBaseTransport, token: str):
        self._transport = transport
        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "X-ID-Token": token,
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.headers.update(self._auth_headers)
        return await self._transport.handle_async_request(request)


@asynccontextmanager
async def _transport_with_auth(mcp_url: str, token: str):
    """Send token on every MCP request: Authorization (for direct targets) and X-ID-Token (for Gateway interceptor)."""
    timeout = httpx.Timeout(30.0, read=300.0)
    base = httpx.AsyncHTTPTransport()
    wrapped = _AuthHeaderTransport(base, token)
    async with httpx.AsyncClient(transport=wrapped, timeout=timeout, follow_redirects=True) as client:
        async with streamable_http_client(mcp_url, http_client=client) as streams:
            yield streams


def create_streamable_http_transport(mcp_url: str, token: str):
    """Return transport context manager that sends id_token on all MCP requests."""
    return _transport_with_auth(mcp_url, token)


def get_full_tools_list(client: MCPClient):
    """Get all tools from MCP client with pagination support."""
    more_tools = True
    tools = []
    pagination_token = None
    while more_tools:
        tmp_tools = client.list_tools_sync(pagination_token=pagination_token)
        tools.extend(tmp_tools)
        if tmp_tools.pagination_token is None:
            more_tools = False
        else:
            pagination_token = tmp_tools.pagination_token
    return tools


@tool
def get_weather():
    """Get the weather."""
    return "Weather is sunny"


model = BedrockModel(model_id=MODEL_ID)

# Static tools only; dynamic gateway tools are added per-request in the entrypoint
STATIC_TOOLS = [get_weather]
DEFAULT_SYSTEM_PROMPT = (
    "You're a helpful assistant. Use the available tools to answer the user. "
    "When HR or employee data is requested, use the tools provided by the HR system."
)


@app.entrypoint
def strands_agent_bedrock(payload, context):
    """
    Invoke the agent with dynamic tools from HR MCP Gateway.
    Uses access_token or id_token from the payload for gateway auth (no Cognito).
    """
    user_input = payload.get("prompt", "").strip()
    access_token = payload.get("access_token")
    if isinstance(access_token, dict):
        access_token = access_token.get("access_token", "")
    access_token = (access_token or "").strip()
    id_token = (payload.get("id_token") or "").strip()
    token = id_token or access_token
    if not token:
        return "Authentication required. No id_token or access_token provided."

    if not HR_MCP_GATEWAY_URL:
        agent = Agent(model=model, tools=STATIC_TOOLS, system_prompt=DEFAULT_SYSTEM_PROMPT)
        response = agent(user_input)
        return response.message["content"][0]["text"]

    try:
        mcp_client = MCPClient(lambda: create_streamable_http_transport(HR_MCP_GATEWAY_URL, token))
        with mcp_client:
            gateway_tools = get_full_tools_list(mcp_client)
            all_tools = STATIC_TOOLS + gateway_tools
            agent = Agent(
                model=model,
                tools=all_tools,
                system_prompt=DEFAULT_SYSTEM_PROMPT,
            )
            invocation_state = {"access_token": access_token or "", "id_token": id_token or ""}
            response = agent(user_input, invocation_state=invocation_state)
        return response.message["content"][0]["text"]
    except Exception as e:
        logger.exception("Gateway or agent error")
        # Check this exception and its cause chain for 500 (e.g. MCPClientInitializationError -> HTTPStatusError)
        err_parts = [str(e)]
        exc = e
        while getattr(exc, "__cause__", None) or getattr(exc, "__context__", None):
            exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
            if exc:
                err_parts.append(str(exc))
        err_msg = " ".join(err_parts)
        if "500" in err_msg or "Internal Server Error" in err_msg:
            return (
                "Gateway returned 500. Often caused by the interceptor Lambda failing or "
                "missing config: ensure the Lambda is attached (ATTACH_INTERCEPTOR + GATEWAY_* in deploy), "
                "X-ID-Token is allowlisted, passRequestHeaders is true, and check CloudWatch Logs for the Lambda."
            )
        return f"Error: {e}"

if __name__ == "__main__":
    app.run()