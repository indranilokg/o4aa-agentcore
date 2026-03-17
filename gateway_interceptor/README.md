# Gateway Interceptor Lambda (Authorization header to MCP target)

Because the [AgentCore Gateway does not forward the `Authorization` header](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html) from the client to the target, the MCP server receives requests without auth and returns 401. This Lambda runs as an **interceptor**: it receives the request, reads the token from an allowlisted header (`X-ID-Token`), and returns it as `Authorization: Bearer <token>`. The Gateway then forwards that header to the MCP target.

## Flow

```
Agent (sends X-ID-Token) → Gateway → Interceptor Lambda → Gateway adds Authorization → MCP target
```

1. Your Strands agent sends every MCP request with `X-ID-Token: <id_token>` (already in `agent.py`).
2. The Gateway calls this Lambda with the incoming request.
3. The Lambda reads `X-ID-Token` from the request and returns `transformedGatewayRequest.headers["Authorization"] = "Bearer <token>"`.
4. The Gateway forwards the request to the MCP target **with** the `Authorization` header.

## Prerequisites

- Gateway target already created for your HR MCP server.
- `X-ID-Token` must be allowlisted for the **incoming** path (Gateway → Lambda). If your Gateway API allows configuring “headers to pass to interceptor,” add `X-ID-Token`. Otherwise the Lambda may receive headers by default; adjust the event shape in `lambda_function.py` if needed.

## 1. Deploy the Lambda

### Option A: AWS Console

1. Create a new Lambda function (Python 3.11+).
2. Paste the contents of `lambda_function.py` as the handler code.
3. Handler: `lambda_function.lambda_handler`.
4. No extra dependencies required (stdlib only).

### Option B: AWS CLI / SAM / Terraform

Package and deploy as usual. Example with AWS CLI:

```bash
cd gateway_interceptor
zip -j function.zip lambda_function.py
aws lambda create-function \
  --function-name agentcore-gateway-mcp-auth-interceptor \
  --runtime python3.11 \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://function.zip \
  --role arn:aws:iam::YOUR_ACCOUNT:role/YOUR_LAMBDA_ROLE
```

## 2. Allowlist `X-ID-Token` for the Lambda (if required)

If the Gateway only passes allowlisted headers to the interceptor, add `X-ID-Token` to that allowlist in the Gateway configuration (e.g. “request headers to pass to interceptor” or equivalent). The exact setting name depends on the Gateway UI/API.

## 3. Attach the Interceptor to the Gateway

The Gateway is configured via the **control plane** API: **`bedrock-agentcore-control`** (not the data-plane `bedrock-agentcore`). You must set **`passRequestHeaders: true`** so the Lambda receives the request headers (including `X-ID-Token`).

### Option A: AWS CLI (update existing gateway)

Replace `GATEWAY_ID`, `GATEWAY_NAME`, `ROLE_ARN`, `LAMBDA_ARN`, and `--authorizer-configuration` with your values. You must re-send the gateway’s current `name`, `role-arn`, `protocol-type`, and `authorizer-configuration`; only `--interceptor-configurations` is new.

```bash
aws bedrock-agentcore-control update-gateway \
  --region us-east-2 \
  --gateway-identifier YOUR_GATEWAY_ID \
  --name YOUR_GATEWAY_NAME \
  --role-arn arn:aws:iam::ACCOUNT_ID:role/YOUR_GATEWAY_ROLE \
  --protocol-type MCP \
  --authorizer-type CUSTOM_JWT \
  --authorizer-configuration '{
    "customJWTAuthorizer": {
      "discoveryUrl": "https://your-okta-domain/.well-known/openid-configuration",
      "allowedAudience": ["your-audience"]
    }
  }' \
  --interceptor-configurations '[{
    "interceptor": {
      "lambda": {
        "arn": "arn:aws:lambda:us-east-2:ACCOUNT_ID:function:agentcore-gateway-mcp-auth-interceptor"
      }
    },
    "interceptionPoints": ["REQUEST"],
    "inputConfiguration": {
      "passRequestHeaders": true
    }
  }]'
```

- **`gateway-identifier`** – Your gateway ID (e.g. from the console or `list-gateways`).
- **`interceptionPoints": ["REQUEST"]`** – Request interceptor only (no response).
- **`passRequestHeaders": true`** – Required so the Lambda gets `X-ID-Token` from the request.

### Option B: Boto3 (Python)

```python
import boto3

client = boto3.client("bedrock-agentcore-control", region_name="us-east-2")

client.update_gateway(
    gatewayIdentifier="YOUR_GATEWAY_ID",
    name="YOUR_GATEWAY_NAME",
    roleArn="arn:aws:iam::ACCOUNT_ID:role/YOUR_GATEWAY_ROLE",
    protocolType="MCP",
    authorizerType="CUSTOM_JWT",
    authorizerConfiguration={
        "customJWTAuthorizer": {
            "discoveryUrl": "https://your-okta-domain/.well-known/openid-configuration",
            "allowedAudience": ["your-audience"],
        }
    },
    interceptorConfigurations=[
        {
            "interceptor": {
                "lambda": {
                    "arn": "arn:aws:lambda:us-east-2:ACCOUNT_ID:function:agentcore-gateway-mcp-auth-interceptor"
                }
            },
            "interceptionPoints": ["REQUEST"],
            "inputConfiguration": {"passRequestHeaders": True},
        }
    ],
)
```

### New gateway (with interceptor)

To create a new gateway with the interceptor attached, use **`create-gateway`** with the same `interceptorConfigurations` (and your authorizer/role). See [Gateway interceptors configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-configuration.html).

### Lambda permission

Ensure the Gateway’s execution role (or the AgentCore service) can invoke your Lambda. If the console prompts to add a resource-based policy, accept it; otherwise add a resource policy so `bedrock-agentcore-control` (or the gateway’s invoke role) has `lambda:InvokeFunction` on your interceptor Lambda.

## 4. Target configuration

Your MCP **target** does **not** need to allowlist `Authorization` for propagation. The docs state that when the interceptor returns `Authorization`, the Gateway forwards it to the target automatically. No need to add `Authorization` to the target’s `allowedRequestHeaders`.

## 5. Verify

1. Invoke the agent and trigger an MCP tool call (e.g. HR tool).
2. The MCP server should receive `Authorization: Bearer <id_token>` and return 200 instead of 401.
3. Check CloudWatch Logs for the Lambda to confirm it receives `X-ID-Token` and returns the transformed request.

## Troubleshooting 500 from the Gateway

If the agent gets **500 Internal Server Error** when calling the Gateway (MCP URL), the failure is in the Gateway or the interceptor Lambda, not in the agent. Check:

1. **Interceptor attached** – Gateway has an REQUEST interceptor with this Lambda (via deploy script with `ATTACH_INTERCEPTOR` and `GATEWAY_*` / `INTERCEPTOR_LAMBDA_ARN`, or via AWS CLI/console).
2. **X-ID-Token allowlisted** – The Gateway is configured to pass `X-ID-Token` to the interceptor (e.g. “request headers to pass to interceptor”).
3. **passRequestHeaders: true** – In `interceptorConfigurations[].inputConfiguration`, `passRequestHeaders` is `true` so the Lambda receives headers.
4. **Lambda permission** – The Gateway (or its role) has `lambda:InvokeFunction` on this Lambda.
5. **CloudWatch Logs** – In the Lambda’s log group, look for the event shape (e.g. missing `headers` or `X-ID-Token`), uncaught exceptions, or timeout. Adjust `lambda_function.py` if the event path for headers differs.

## Event shape (for debugging)

If the Lambda does not receive headers, log `event` and adjust the token read logic. Typical shape:

- `event["mcp"]["gatewayRequest"]["body"]` – MCP request body.
- `event["mcp"]["gatewayRequest"]["headers"]` – request headers (if the Gateway passes them to the interceptor).

If headers are under a different path (e.g. `event["requestContext"]["request"]["headers"]`), update `lambda_function.py` to read `X-ID-Token` from that path.
