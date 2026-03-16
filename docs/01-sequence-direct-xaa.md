# Sequence Diagram: Agent → Okta XAA–Protected MCP Server (Direct)

Agent calls the MCP server **directly** (no Gateway). The agent performs Cross-App Access (XAA) to exchange the user's ID token for an auth-server access token before calling MCP.

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant App as Web App
    participant Okta as Okta
    participant Runtime as AgentCore Runtime
    participant Agent as Strands Agent
    participant XAA as Okta (Cross App Access)
    participant MCP as External MCP Server (XAA Protected)

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

    Agent->>Agent: XAA configured?
    Agent->>XAA: Token exchange (id_token, audience=Custom AS)
    XAA->>XAA: ID-JAG: id_token → id_jag_token
    XAA->>XAA: Resume: id_jag_token → access_token
    XAA->>Agent: access_token (Custom AS)

    Agent->>MCP: HTTPS + Authorization: Bearer access_token
    MCP->>MCP: Validate token (Custom AS)
    MCP->>Agent: Tools list / tool results
    Agent->>Runtime: Response (text)
    Runtime->>App: Response
    App->>User: Display reply
```
