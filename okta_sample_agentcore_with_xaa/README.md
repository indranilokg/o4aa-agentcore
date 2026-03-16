# okta_sample_agentcore_with_xaa

AgentCore MCP adapter agent that calls the MCP server **directly** (no gateway).

- **Without XAA**: sends `Authorization: Bearer <id_token>` to the MCP server.
- **With XAA (Cross-App Access)**: before calling MCP, the agent exchanges the user's ID token for an auth-server access token via ID-JAG (Identity Assertion Authorization Grant), then sends `Authorization: Bearer <access_token>` to the MCP server. Configure `XAA_*` env vars to enable.

Deploys as agent name **okta_sample_agentcore_with_xaa**.

## Deploy

1. Copy `.env.example` to `.env` and set values (AWS credentials, DISCOVERY_URL, OKTA_AUDIENCE, MODEL_ID, HR_MCP_SERVER_URL). To enable XAA, set XAA_OKTA_DOMAIN, XAA_PRINCIPAL_ID, XAA_AUTHORIZATION_SERVER_ID, and XAA_PRIVATE_JWK (see `.env.example`). XAA uses **okta-client-python** (same as `cross_app_access_demo_official_sdk.ipynb`).
2. Run:

```bash
cd okta_sample_agentcore_with_xaa
pip install -r requirements.txt   # or use a venv
python agent_deployement.py
```

3. Copy the output `AGENT_RUNTIME_ARN` (or runtime ARN) to your App `.env` if using the same Flask app.

## Local run

```bash
export LOCAL_DEV=1
python agent.py
```

Then call `http://localhost:8080/invocations` with a JSON body containing `prompt`, `id_token`, and optionally `access_token`.
