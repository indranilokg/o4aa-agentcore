# Gateway XAA Interceptor Lambda (multi-agent, Secrets Manager)

Unlike `gateway_interceptor` (which forwards the raw `id_token` as-is), this interceptor performs **Okta Cross-App Access (XAA)** inside the Lambda: it receives the user's `id_token` via `X-ID-Token`, exchanges it for a **custom authorization server access_token**, and returns that access_token as the `Authorization` header.

**Multi-agent support:** Each agent sends an `X-Agent-ID` header. The Lambda fetches that agent's XAA credentials from **AWS Secrets Manager** — one secret per agent, stored under a common prefix. No credentials in env vars or code.

## Flow

```
Agent (sends X-ID-Token + X-Agent-ID: "hr-agent")
  → Gateway
    → this Lambda
      1. Read X-Agent-ID → "hr-agent"
      2. Fetch secret: agentcore/xaa/hr-agent  (from Secrets Manager)
      3. XAA exchange: id_token → custom AS access_token
    → Gateway adds Authorization: Bearer <access_token>
      → MCP target
```

## Prerequisites

- Gateway target already created for your MCP server.
- `X-ID-Token` **and** `X-Agent-ID` must be allowlisted for the Gateway → Lambda path.
- Okta: service app with private JWK, custom authorization server, Cross-App Access enabled.
- AWS Secrets Manager: one secret per agent under a shared prefix.

## 1. Create secrets in Secrets Manager

Each agent gets its own secret. The secret name follows the pattern `<prefix>/<agent-id>`:

### Option A: AWS Console (Manual)

1. Open **AWS Secrets Manager** in your AWS Console.
2. Click **"Store a new secret"**.
3. Choose **"Other type of secret"** (not RDS/Redshift).
4. Switch to the **Plaintext** tab and paste the JSON object:

```json
{
  "okta_domain": "https://dev-12345.okta.com",
  "principal_id": "0oaServiceApp1",
  "authorization_server_id": "ausXXX",
  "scope": "mcp:read",
  "private_jwk": {
    "kty": "RSA",
    "kid": "your-key-id",
    "n": "...",
    "e": "AQAB",
    "d": "...",
    "p": "...",
    "q": "...",
    "dp": "...",
    "dq": "...",
    "qi": "..."
  }
}
```

5. Click **Next**.
6. Set **Secret name** to `agentcore/xaa/<agent-id>` (e.g., `agentcore/xaa/hr-agent`).
7. Leave encryption as default (`aws/secretsmanager` KMS key) unless you need a custom key.
8. Click **Next** → skip rotation → **Store**.

To **update** an existing secret later (e.g., key rotation), open the secret in the Console → "Retrieve secret value" → **Edit**, or use the CLI:

```bash
aws secretsmanager put-secret-value \
  --secret-id "agentcore/xaa/hr-agent" \
  --secret-string '{ ... updated JSON ... }'
```

### Option B: AWS CLI

```bash
# hr-agent
aws secretsmanager create-secret \
  --name "agentcore/xaa/hr-agent" \
  --secret-string '{
    "okta_domain": "https://dev-12345.okta.com",
    "principal_id": "0oaServiceApp1",
    "authorization_server_id": "ausXXX",
    "scope": "mcp:read",
    "private_jwk": {"kty":"RSA","kid":"...","n":"...","d":"..."}
  }' \
  --region us-east-2

# finance-agent
aws secretsmanager create-secret \
  --name "agentcore/xaa/finance-agent" \
  --secret-string '{
    "okta_domain": "https://dev-67890.okta.com",
    "principal_id": "0oaServiceApp2",
    "authorization_server_id": "ausYYY",
    "scope": "mcp:read mcp:write",
    "private_jwk": {"kty":"RSA","kid":"...","n":"...","d":"..."}
  }' \
  --region us-east-2
```

Each secret value is a JSON object with these fields:

| Field | Required | Description |
|---|---|---|
| `okta_domain` | Yes | Okta org URL |
| `principal_id` | Yes | Service app / workload `client_id` in Okta |
| `authorization_server_id` | Yes | Custom authorization server ID |
| `scope` | No | Space-separated scopes (default: `mcp:read`) |
| `private_jwk` | Yes | Private JWK object for client assertion signing |

## 2. Agent-side: send `X-Agent-ID`

Each agent must include `X-Agent-ID` in its MCP transport headers:

```python
self._auth_headers = {
    "Authorization": f"Bearer {token}",
    "X-ID-Token": token,
    "X-Agent-ID": "hr-agent",   # must match a secret name suffix
}
```

## 3. Deploy the Lambda

### Option A: Lambda Layer

```bash
cd gateway_xaa_interceptor

# Build the layer (okta-client-python + aiohttp)
mkdir -p layer/python
pip install -r requirements.txt -t layer/python
cd layer && zip -r ../xaa-layer.zip python && cd ..

# Create the layer
aws lambda publish-layer-version \
  --layer-name okta-xaa-layer \
  --zip-file fileb://xaa-layer.zip \
  --compatible-runtimes python3.11 python3.12 python3.13 \
  --region us-east-2

# Create the function
zip -j function.zip lambda_function.py
aws lambda create-function \
  --function-name agentcore-gateway-xaa-interceptor \
  --runtime python3.11 \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://function.zip \
  --role arn:aws:iam::YOUR_ACCOUNT:role/YOUR_LAMBDA_ROLE \
  --timeout 30 \
  --layers arn:aws:lambda:us-east-2:YOUR_ACCOUNT:layer:okta-xaa-layer:1 \
  --environment "Variables={XAA_SECRET_PREFIX=agentcore/xaa}" \
  --region us-east-2
```

### Option B: AWS Console

1. Create a new Lambda function (Python 3.11+, timeout ≥ 30s).
2. Paste `lambda_function.py` as the handler code.
3. Create a Lambda Layer from `xaa-layer.zip` and attach it.
4. Set env var: `XAA_SECRET_PREFIX=agentcore/xaa` (only the prefix, no secrets here).
5. Handler: `lambda_function.lambda_handler`.

## 4. IAM — Lambda execution role

The Lambda role needs `secretsmanager:GetSecretValue` on the agent secrets:

```json
{
  "Effect": "Allow",
  "Action": "secretsmanager:GetSecretValue",
  "Resource": "arn:aws:secretsmanager:us-east-2:YOUR_ACCOUNT:secret:agentcore/xaa/*"
}
```

## 5. Environment variables

| Variable | Required | Description |
|---|---|---|
| `XAA_SECRET_PREFIX` | No | Secrets Manager prefix (default: `agentcore/xaa`). Secret name = `<prefix>/<agent_id>` |

**Single-agent fallback** (no Secrets Manager needed, for dev/testing):

| Variable | Description |
|---|---|
| `XAA_OKTA_DOMAIN` | Okta org URL |
| `XAA_PRINCIPAL_ID` | Service app client_id |
| `XAA_AUTHORIZATION_SERVER_ID` | Custom AS ID |
| `XAA_SCOPE` | Scopes (default: `mcp:read`) |
| `XAA_PRIVATE_JWK` | Private JWK JSON string |

## 6. Gateway configuration

Allowlist **both** `X-ID-Token` and `X-Agent-ID` for the interceptor:

- `passRequestHeaders: true` in `inputConfiguration`.
- `interceptionPoints: ["REQUEST"]`.
- Gateway / AgentCore must have `lambda:InvokeFunction` permission on this Lambda.

## 7. Caching behavior

Secrets are cached **in-memory** for the Lambda execution environment lifetime:
- **Cold start** → fetches from Secrets Manager (adds ~100–200ms).
- **Warm invocations** → uses cache (no Secrets Manager call).
- **After key rotation** → new Lambda instances pick up the new secret automatically. To force all instances to refresh, update the Lambda config (triggers cold starts).

## 8. Config resolution order

1. `X-Agent-ID` present → fetch `<prefix>/<agent_id>` from Secrets Manager → XAA exchange.
2. No `X-Agent-ID` or secret not found → fall back to flat env vars (single-agent).
3. Nothing configured → forward raw `id_token` as-is (passthrough).

## 9. Adding a new agent

1. Create a secret: `aws secretsmanager create-secret --name "agentcore/xaa/<new-agent-id>" --secret-string '{...}'`
2. Agent sends `X-Agent-ID: <new-agent-id>` in its headers.
3. No Lambda redeployment needed — the Lambda fetches the new secret on first request.

## Differences from `gateway_interceptor`

| | `gateway_interceptor` | `gateway_xaa_interceptor` |
|---|---|---|
| **Token on MCP target** | Raw `id_token` | Custom AS `access_token` (via XAA) |
| **Multi-agent** | No | Yes (`X-Agent-ID` + per-agent secrets) |
| **Secrets** | None | AWS Secrets Manager (one per agent) |
| **Dependencies** | None (stdlib only) | `okta-client-python`, `aiohttp`, `boto3` |
| **Deployment** | Single file paste | Layer or container |
| **Lambda timeout** | Default (3s) OK | ≥ 30s recommended |

## Troubleshooting

1. **"Secret agentcore/xaa/X not found"** — Secret doesn't exist or name doesn't match `<prefix>/<agent_id>`.
2. **"Lambda role lacks secretsmanager:GetSecretValue"** — Add the IAM permission (see section 4).
3. **"No X-Agent-ID header"** — Agent isn't sending the header, or it's not allowlisted on the Gateway.
4. **XAA exchange fails** — Check private JWK matches the Okta public key, custom AS has the scopes, Cross-App Access is enabled.
5. **Timeout** — Increase Lambda timeout (≥ 30s); cold start + XAA = two Okta round-trips.
6. **Gateway 500** — Interceptor attached? `passRequestHeaders: true`? Lambda invoke permission?
