# Architecture: Agent → MCP via AgentCore Gateway + XAA Interceptor Lambda

The agent connects to the MCP server **through** an AgentCore Gateway. A **XAA Interceptor Lambda** performs the Okta Cross-App Access (ID-JAG) token exchange inside the Gateway's request pipeline — no external adapter needed. The Lambda identifies the calling agent via `X-Agent-ID`, fetches per-agent XAA credentials from **AWS Secrets Manager**, and exchanges the user's `id_token` for a properly-scoped **custom authorization server access_token** before the request reaches the MCP target.

```mermaid
flowchart TB
    subgraph User[" "]
        U["User / Browser"]
    end

    subgraph Client["Client - your infrastructure"]
        App["Web App - Flask"]
    end

    subgraph Okta["Okta"]
        OIDC["OAuth 2.0 / OIDC"]
        OrgAS["Org Authorization Server"]
        CustomAS["Custom Authorization Server"]
    end

    subgraph AWS["AWS Bedrock AgentCore"]
        Runtime["AgentCore Runtime"]
        Agent["Strands Agent"]
        Gateway["AgentCore Gateway"]
        Lambda["XAA Interceptor Lambda"]
    end

    subgraph Secrets["AWS Secrets Manager"]
        SM["Per-agent XAA credentials"]
    end

    subgraph MCPHost["Target MCP - Custom AS protected"]
        MCP["MCP Server"]
    end

    U -->|Sign in| App
    App <-->|OAuth tokens| OIDC
    App -->|Invoke prompt, id_token| Runtime
    Runtime --> Agent
    Agent -->|MCP request: X-ID-Token, X-Agent-ID| Gateway
    Gateway -->|Intercept request| Lambda
    Lambda -->|Fetch agent secret| SM
    SM -->|XAA config: domain, principal_id, private_jwk| Lambda
    Lambda -->|XAA: id_token → access_token| OrgAS
    OrgAS -->|id_jag_token| Lambda
    Lambda -->|Resume: id_jag_token → access_token| CustomAS
    CustomAS -->|access_token| Lambda
    Lambda -->|Authorization: Bearer access_token| Gateway
    Gateway -->|Bearer access_token| MCP
    MCP -->|Tools and results| Gateway
    Gateway -->|Response| Agent
    Agent -->|Response| Runtime
    Runtime -->|Response| App
    App -->|UI| U
```

## Components

| Component | Role |
|-----------|------|
| **Web App** | Okta OAuth login; sends user message and `id_token` to AgentCore. |
| **AgentCore Runtime** | Hosts the Strands agent; passes payload including `id_token` to the agent entrypoint. |
| **Strands Agent** | Sends MCP requests to **Gateway** with `X-ID-Token` and `X-Agent-ID` headers. Does **not** perform XAA itself. |
| **AgentCore Gateway** | Proxies MCP traffic to the target; does not forward client `Authorization`; invokes the XAA interceptor Lambda. |
| **XAA Interceptor Lambda** | Reads `X-Agent-ID`; fetches credentials from Secrets Manager; performs XAA (ID-JAG) exchange; returns `Authorization: Bearer <access_token>`. Caches secrets in-memory for warm invocations. |
| **AWS Secrets Manager** | Stores per-agent XAA config: `okta_domain`, `principal_id`, `authorization_server_id`, `scope`, `private_jwk`. One secret per agent: `agentcore/xaa/<agent-id>`. |
| **Okta Token Exchange** | Org AS: client assertion + id_token → ID-JAG token. Custom AS: ID-JAG → scoped access_token. |
| **Target MCP Server** | Protected by Okta Custom Authorization Server; validates the access token. |

## Multi-Agent Support

```mermaid
flowchart LR
    subgraph Agents["Multiple Agents"]
        A1["HR Agent\nX-Agent-ID: hr-agent"]
        A2["Finance Agent\nX-Agent-ID: finance-agent"]
        A3["IT Agent\nX-Agent-ID: it-agent"]
    end

    subgraph GW["AgentCore Gateway"]
        Lambda["XAA Interceptor Lambda"]
    end

    subgraph SM["Secrets Manager"]
        S1["agentcore/xaa/hr-agent"]
        S2["agentcore/xaa/finance-agent"]
        S3["agentcore/xaa/it-agent"]
    end

    A1 --> Lambda
    A2 --> Lambda
    A3 --> Lambda
    Lambda --> S1
    Lambda --> S2
    Lambda --> S3
```

Each agent identifies itself via `X-Agent-ID`. The Lambda fetches the corresponding secret, which contains the Okta credentials specific to that agent (different service apps, scopes, or even different Okta orgs). No Lambda redeployment is needed when adding a new agent — just create a new secret in Secrets Manager.
