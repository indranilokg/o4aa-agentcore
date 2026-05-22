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

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

HR_MCP_GATEWAY_URL = os.getenv("HR_MCP_GATEWAY_URL", "").strip()
MODEL_ID = os.getenv("MODEL_ID", "")
AGENT_ID = os.getenv("AGENT_ID", "").strip()


class _AuthHeaderTransport(httpx.AsyncBaseTransport):
    """Injects Authorization, X-ID-Token, and X-Agent-ID on every request.
    The Gateway strips Authorization but passes X-ID-Token and X-Agent-ID to the
    XAA interceptor Lambda, which performs the Okta XAA exchange and returns
    Authorization: Bearer <custom-AS-access-token>."""

    def __init__(self, transport: httpx.AsyncBaseTransport, token: str, agent_id: str):
        self._transport = transport
        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "X-ID-Token": token,
            "X-Agent-ID": agent_id,
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.headers.update(self._auth_headers)
        return await self._transport.handle_async_request(request)


@asynccontextmanager
async def _transport_with_auth(mcp_url: str, token: str, agent_id: str):
    """Send token + agent ID on every MCP request via the Gateway."""
    timeout = httpx.Timeout(30.0, read=300.0)
    base = httpx.AsyncHTTPTransport()
    wrapped = _AuthHeaderTransport(base, token, agent_id)
    async with httpx.AsyncClient(transport=wrapped, timeout=timeout, follow_redirects=True) as client:
        async with streamable_http_client(mcp_url, http_client=client) as streams:
            yield streams


def create_streamable_http_transport(mcp_url: str, token: str, agent_id: str):
    return _transport_with_auth(mcp_url, token, agent_id)


def get_full_tools_list(client: MCPClient):
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

STATIC_TOOLS = [get_weather]
DEFAULT_SYSTEM_PROMPT = (
    "You're a helpful assistant. Use the available tools to answer the user. "
    "When HR or employee data is requested, use the tools provided by the HR system."
)


@app.entrypoint
def strands_agent_bedrock(payload, context):
    """
    Invoke the agent with dynamic tools from HR MCP Gateway.
    Sends X-Agent-ID so the XAA interceptor Lambda can look up this agent's
    Okta credentials from Secrets Manager and perform the XAA exchange.
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

    agent_id = AGENT_ID
    if not agent_id:
        logger.warning("AGENT_ID env var not set; X-Agent-ID header will be empty")

    if not HR_MCP_GATEWAY_URL:
        agent = Agent(model=model, tools=STATIC_TOOLS, system_prompt=DEFAULT_SYSTEM_PROMPT)
        response = agent(user_input)
        return response.message["content"][0]["text"]

    try:
        mcp_client = MCPClient(
            lambda: create_streamable_http_transport(HR_MCP_GATEWAY_URL, token, agent_id)
        )
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
        err_parts = [str(e)]
        exc = e
        while getattr(exc, "__cause__", None) or getattr(exc, "__context__", None):
            exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
            if exc:
                err_parts.append(str(exc))
        err_msg = " ".join(err_parts)
        if "500" in err_msg or "Internal Server Error" in err_msg:
            return (
                "Gateway returned 500. Check the XAA interceptor Lambda in CloudWatch: "
                "ensure the Lambda is attached, X-ID-Token and X-Agent-ID are allowlisted, "
                "passRequestHeaders is true, and the agent's secret exists in Secrets Manager."
            )
        return f"Error: {e}"


if __name__ == "__main__":
    app.run()
