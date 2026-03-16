# Sequence Diagram: Agent → Okta XAA–Protected MCP via AgentCore Gateway + Okta MCP Adapter

Agent reaches the MCP server **through** an AgentCore Gateway, then the **Okta MCP Adapter** proxy. The Gateway does not forward the client `Authorization` header; a **Lambda interceptor** reads the token from an allowlisted header (e.g. `X-ID-Token`) and adds `Authorization: Bearer <token>` so the Gateway forwards the request to the **Okta MCP Adapter**. The adapter validates the incoming id token, performs **XAA (ID-JAG)** at the proxy layer, and forwards the request to the **target MCP server** with the backend access token. See [Okta MCP Adapter architecture](https://github.com/indranilokg/okta-agent-mcp-adapter/blob/main/docs/ARCHITECTURE_DIAGRAM.md).

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant App as Web App
    participant Okta as Okta
    participant Runtime as AgentCore Runtime
    participant Agent as Strands Agent
    participant Gateway as AgentCore Gateway
    participant Lambda as Interceptor Lambda
    participant Adapter as Okta MCP Adapter
    participant MCP as Target MCP Server

    User->>App: Access app
    App->>Okta: Redirect to login
    Okta->>User: Sign in
    User->>Okta: Credentials
    Okta->>App: Redirect with auth code
    App->>Okta: Exchange code for tokens
    Okta->>App: id_token

    User->>App: Send message
    App->>Runtime: Invoke agent - prompt, id_token
    Runtime->>Agent: Entrypoint - payload with id_token

    Note over Agent: Agent passes id_token in X-ID-Token
    Agent->>Gateway: MCP request - X-ID-Token: id_token
    Gateway->>Lambda: Intercept request - headers X-ID-Token
    Lambda->>Lambda: Read X-ID-Token, set Authorization
    Lambda->>Gateway: transformedGatewayRequest - Authorization Bearer id_token
    Gateway->>Adapter: MCP request - Authorization Bearer id_token

    Note over Adapter: Validate JWT, perform XAA - ID-JAG
    Adapter->>Adapter: Validate id_token - Okta JWKS
    Adapter->>Adapter: Token exchange - id_token to backend access_token
    Adapter->>MCP: MCP request - Authorization Bearer access_token
    MCP->>MCP: Validate token - Custom AS
    MCP->>Adapter: Tool list or tool results
    Adapter->>Gateway: Response
    Gateway->>Agent: Response
    Agent->>Runtime: Response text
    Runtime->>App: Response
    App->>User: Display reply
```
