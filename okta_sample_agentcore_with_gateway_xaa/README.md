# okta_sample_agentcore_with_gateway_xaa

Strands agent that connects to an MCP server **through** an AgentCore Gateway with **XAA (Cross-App Access)** token exchange handled by the **`gateway_xaa_interceptor`** Lambda.

Unlike `okta_sample_agentcore_with_gateway` (which forwards the raw `id_token`), this agent sends an **`X-Agent-ID`** header so the XAA interceptor Lambda can look up this agent's Okta credentials from **AWS Secrets Manager** and exchange the `id_token` for a properly-scoped **custom AS access_token** before it reaches the MCP target.

## How it works

```
Agent (sends X-ID-Token + X-Agent-ID: "hr-agent")
  → AgentCore Gateway
    → XAA Interceptor Lambda
      1. Reads X-Agent-ID → fetches secret: agentcore/xaa/hr-agent
      2. XAA exchange: id_token → custom AS access_token
    → Gateway adds Authorization: Bearer <access_token>
      → MCP target (validates custom AS token)
```

## Prerequisites

1. **XAA Interceptor Lambda** deployed — see [`gateway_xaa_interceptor/README.md`](../gateway_xaa_interceptor/README.md).
2. **Agent's secret** in Secrets Manager: `agentcore/xaa/<AGENT_ID>` with the XAA config JSON.
3. **Gateway** configured with the XAA interceptor attached (`passRequestHeaders: true`, `X-ID-Token` and `X-Agent-ID` allowlisted).

## Deploy

1. Copy `.env.example` to `.env` and fill in values:
   - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`
   - `DISCOVERY_URL`, `OKTA_AUDIENCE` — for Gateway JWT validation
   - `HR_MCP_GATEWAY_URL` — your Gateway MCP endpoint
   - `AGENT_ID` — must match the Secrets Manager secret name suffix (e.g. `hr-agent`)
   - `MODEL_ID` — Bedrock model to use

2. Run:
   ```bash
   python agent_deployement.py
   ```

3. Copy the output `AGENT_RUNTIME_ARN` to your App `.env`.

## Attach XAA interceptor to the Gateway

After deploying the XAA interceptor Lambda, you can have the deployment script attach it automatically:

1. In `.env`, set:
   - `ATTACH_INTERCEPTOR=true`
   - `GATEWAY_ID` — your Gateway ID
   - `GATEWAY_NAME` — Gateway name
   - `GATEWAY_ROLE_ARN` — IAM role ARN used by the Gateway
   - `XAA_INTERCEPTOR_LAMBDA_ARN` — ARN of the deployed XAA interceptor Lambda

2. Run `python agent_deployement.py`. The script calls `update_gateway` with the interceptor configuration.

## Differences from `okta_sample_agentcore_with_gateway`

| | `okta_sample_agentcore_with_gateway` | `okta_sample_agentcore_with_gateway_xaa` |
|---|---|---|
| **Interceptor** | `gateway_interceptor` (id_token passthrough) | `gateway_xaa_interceptor` (XAA exchange) |
| **Token on MCP target** | Raw `id_token` | Custom AS `access_token` |
| **X-Agent-ID header** | Not sent | Sent (identifies agent for secret lookup) |
| **AGENT_ID env var** | N/A | Required (matches Secrets Manager secret) |
| **Lambda env var** | `INTERCEPTOR_LAMBDA_ARN` | `XAA_INTERCEPTOR_LAMBDA_ARN` |
