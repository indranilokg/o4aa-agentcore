# okta_sample_agentcore_with_gateway

Strands agent that connects to the MCP server **through** an AgentCore Gateway. The agent sends `X-ID-Token` on each MCP request; the Gateway does not forward `Authorization`, so a **Lambda interceptor** (see `gateway_interceptor/`) is used to add `Authorization: Bearer <token>` before the request is sent to the MCP target.

## Deploy

1. Copy `.env.example` to `.env` and set AWS credentials, `DISCOVERY_URL`, `OKTA_AUDIENCE`, `HR_MCP_GATEWAY_URL`, `MODEL_ID`.
2. Run:
   ```bash
   python agent_deployement.py
   ```
3. Copy the output `AGENT_RUNTIME_ARN` to your App `.env`.

## Attach Lambda interceptor to the Gateway

After deploying the **interceptor Lambda** (see [gateway_interceptor/README.md](../../gateway_interceptor/README.md)), you can have the deployment script attach it to your Gateway automatically:

1. In `.env`, set:
   - `ATTACH_INTERCEPTOR=true`
   - `GATEWAY_ID` — your Gateway ID (e.g. from console or `list-gateways`)
   - `GATEWAY_NAME` — Gateway name (must match the name used when the gateway was created)
   - `GATEWAY_ROLE_ARN` — IAM role ARN used by the Gateway
   - `INTERCEPTOR_LAMBDA_ARN` — ARN of the deployed interceptor Lambda (e.g. `arn:aws:lambda:us-east-2:ACCOUNT_ID:function:agentcore-gateway-mcp-auth-interceptor`)
2. Run `python agent_deployement.py` again (or run once with these set). The script will call `bedrock-agentcore-control` `update_gateway` with the interceptor configuration (`passRequestHeaders: true`, `interceptionPoints: ["REQUEST"]`).

If any of these are missing, the script skips the gateway update and prints a message. Ensure the Gateway’s role (or AgentCore) can invoke the Lambda (`lambda:InvokeFunction`).
