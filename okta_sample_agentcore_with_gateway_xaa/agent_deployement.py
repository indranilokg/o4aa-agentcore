import os
import traceback

from dotenv import load_dotenv

load_dotenv()

if not os.getenv("AWS_ACCESS_KEY_ID") or not os.getenv("AWS_SECRET_ACCESS_KEY"):
    raise ValueError(
        "AWS credentials not found. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env"
    )

from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime

boto_session = Session()
region = boto_session.region_name or os.getenv("AWS_DEFAULT_REGION", "us-east-1")

agentcore_runtime = Runtime()
agent_name = "okta_sample_agentcore_with_gateway_xaa"

response = agentcore_runtime.configure(
    entrypoint="agent.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="requirements.txt",
    region=region,
    agent_name=agent_name,
    authorizer_configuration={
        "customJWTAuthorizer": {
            "discoveryUrl": os.getenv("DISCOVERY_URL"),
            "allowedAudience": [os.getenv("OKTA_AUDIENCE")],
        },
    },
)
print("AC runtime response:", response)

env_vars = {
    k: v
    for k, v in {
        "MODEL_ID": os.getenv("MODEL_ID"),
        "HR_MCP_GATEWAY_URL": os.getenv("HR_MCP_GATEWAY_URL"),
        "AGENT_ID": os.getenv("AGENT_ID"),
    }.items()
    if v is not None
}
try:
    launch_result = agentcore_runtime.launch(env_vars=env_vars)
except Exception as e:
    print("Error launching AgentCore runtime:", repr(e))
    traceback.print_exc()
    raise

print("Agent launch results:", launch_result)


# ---------------------------------------------------------------------------
# Optionally attach the XAA interceptor Lambda to the Gateway.
# Requires: ATTACH_INTERCEPTOR=true and GATEWAY_* / XAA_INTERCEPTOR_LAMBDA_ARN in .env.
# Deploy the Lambda first (see gateway_xaa_interceptor/README.md).
# ---------------------------------------------------------------------------

def attach_xaa_interceptor_to_gateway():
    if os.getenv("ATTACH_INTERCEPTOR", "").strip().lower() not in ("1", "true", "yes"):
        return
    gateway_id = os.getenv("GATEWAY_ID", "").strip()
    gateway_name = os.getenv("GATEWAY_NAME", "").strip()
    gateway_role_arn = os.getenv("GATEWAY_ROLE_ARN", "").strip()
    lambda_arn = os.getenv("XAA_INTERCEPTOR_LAMBDA_ARN", "").strip()
    discovery_url = os.getenv("DISCOVERY_URL", "").strip()
    allowed_audience = os.getenv("OKTA_AUDIENCE", "").strip()
    if not all([gateway_id, gateway_name, gateway_role_arn, lambda_arn, discovery_url, allowed_audience]):
        print(
            "ATTACH_INTERCEPTOR is set but one of GATEWAY_ID, GATEWAY_NAME, GATEWAY_ROLE_ARN, "
            "XAA_INTERCEPTOR_LAMBDA_ARN, DISCOVERY_URL, OKTA_AUDIENCE is missing. "
            "Skipping gateway update."
        )
        return
    try:
        control = boto_session.client("bedrock-agentcore-control", region_name=region)
        control.update_gateway(
            gatewayIdentifier=gateway_id,
            name=gateway_name,
            roleArn=gateway_role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "discoveryUrl": discovery_url,
                    "allowedAudience": [allowed_audience],
                },
            },
            interceptorConfigurations=[
                {
                    "interceptor": {
                        "lambda": {"arn": lambda_arn},
                    },
                    "interceptionPoints": ["REQUEST"],
                    "inputConfiguration": {"passRequestHeaders": True},
                },
            ],
        )
        print("XAA interceptor attached to gateway:", gateway_id)
    except Exception as e:
        print("Warning: failed to attach XAA interceptor to gateway:", e)
        traceback.print_exc()


attach_xaa_interceptor_to_gateway()
