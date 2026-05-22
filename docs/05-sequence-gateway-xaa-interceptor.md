# Sequence Diagram: Agent → MCP via AgentCore Gateway + XAA Interceptor Lambda

Agent reaches the MCP server **through** an AgentCore Gateway. The Gateway does not forward the client `Authorization` header; a **XAA Interceptor Lambda** reads the token from `X-ID-Token`, identifies the agent via `X-Agent-ID`, fetches XAA credentials from **AWS Secrets Manager**, performs the **Okta Cross-App Access (ID-JAG)** exchange inside the Lambda, and returns `Authorization: Bearer <custom-AS-access-token>` so the Gateway forwards a properly-scoped token to the MCP target.

Unlike the [Gateway + Okta MCP Adapter pattern](02-sequence-gateway-interceptor.md) (where XAA happens in an external adapter), here the **Lambda itself performs the XAA exchange** — no adapter is needed.

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant App as Web App
    participant Okta as Okta
    participant Runtime as AgentCore Runtime
    participant Agent as Strands Agent
    participant Gateway as AgentCore Gateway
    participant Lambda as XAA Interceptor Lambda
    participant SM as AWS Secrets Manager
    participant OktaXAA as Okta (Cross App Access)
    participant MCP as Target MCP Server

    User->>App: Access app
    App->>Okta: Redirect to login
    Okta->>User: Sign in
    User->>Okta: Credentials
    Okta->>App: Redirect with auth code
    App->>Okta: Exchange code for tokens
    Okta->>App: id_token, access_token

    User->>App: Send message (chat)
    App->>Runtime: Invoke agent (prompt, id_token)
    Runtime->>Agent: Entrypoint (payload with id_token)

    Note over Agent: Sends X-ID-Token + X-Agent-ID
    Agent->>Gateway: MCP request (X-ID-Token, X-Agent-ID: "hr-agent")
    Gateway->>Lambda: Intercept request (headers)

    Note over Lambda: Resolve agent config
    Lambda->>Lambda: Read X-Agent-ID → "hr-agent"
    Lambda->>SM: GetSecretValue("agentcore/xaa/hr-agent")
    SM->>Lambda: XAA config (okta_domain, principal_id, private_jwk, ...)

    Note over Lambda: Perform XAA exchange
    Lambda->>OktaXAA: Client assertion JWT + id_token (ID-JAG flow.start)
    OktaXAA->>OktaXAA: Validate id_token, issue id_jag_token
    OktaXAA->>Lambda: id_jag_token
    Lambda->>OktaXAA: Resume (id_jag_token → Custom AS)
    OktaXAA->>Lambda: access_token (Custom AS)

    Lambda->>Gateway: transformedGatewayRequest (Authorization: Bearer access_token)
    Gateway->>MCP: MCP request (Authorization: Bearer access_token)
    MCP->>MCP: Validate token (Custom AS)
    MCP->>Gateway: Tool list / tool results
    Gateway->>Agent: Response
    Agent->>Runtime: Response (text)
    Runtime->>App: Response
    App->>User: Display reply
```

## Key Differences from Other Patterns

| Aspect | Direct XAA (Pattern 1) | Gateway + Adapter (Pattern 2) | Gateway + XAA Interceptor (Pattern 3) |
|--------|------------------------|-------------------------------|---------------------------------------|
| **Where XAA happens** | Inside the agent | In the Okta MCP Adapter (proxy) | In the Lambda interceptor |
| **Multi-agent** | No (per-agent config) | Adapter handles all agents | Yes (`X-Agent-ID` + Secrets Manager) |
| **Agent complexity** | Agent runs XAA SDK | Agent only passes id_token | Agent only passes id_token + agent ID |
| **Extra infrastructure** | None | Okta MCP Adapter (separate service) | Lambda + Secrets Manager |
| **Token on MCP target** | Custom AS access_token | Custom AS access_token | Custom AS access_token |
