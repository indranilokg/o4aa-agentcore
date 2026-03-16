import os
import traceback

from dotenv import load_dotenv

# Load environment variables from .env file FIRST
load_dotenv()

# Ensure AWS credentials are set in environment
if not os.getenv("AWS_ACCESS_KEY_ID") or not os.getenv("AWS_SECRET_ACCESS_KEY"):
    raise ValueError(
        "AWS credentials not found. Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env file"
    )

# Now import after credentials are set
from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session

# Create boto3 session
boto_session = Session()
region = boto_session.region_name or os.getenv("AWS_DEFAULT_REGION", "us-east-1")

agentcore_runtime = Runtime()
agent_name = "okta_sample_agentcore_with_xaa"

# Configure the agentcore runtime
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
print("AC runtime response", response)

try:
    # Env vars here are injected into the AgentCore runtime container; agent.py reads them via os.getenv()
    # Env vars for agent + optional Cross-App Access (XAA). XAA_* exchange id_token for auth-server token before MCP.
    env_vars = {
        k: v
        for k, v in {
            "MODEL_ID": os.getenv("MODEL_ID"),
            "HR_MCP_SERVER_URL": os.getenv("HR_MCP_SERVER_URL"),
            "XAA_OKTA_DOMAIN": os.getenv("XAA_OKTA_DOMAIN"),
            "XAA_PRINCIPAL_ID": os.getenv("XAA_PRINCIPAL_ID"),
            "XAA_AUTHORIZATION_SERVER_ID": os.getenv("XAA_AUTHORIZATION_SERVER_ID"),
            "XAA_SCOPE": os.getenv("XAA_SCOPE"),
            "XAA_PRIVATE_JWK": os.getenv("XAA_PRIVATE_JWK"),
        }.items()
        if v is not None
    }
    launch_result = agentcore_runtime.launch(env_vars=env_vars)
except Exception as e:
    print("Error launching AgentCore runtime:", repr(e))
    print("Traceback:")
    traceback.print_exc()
    raise

print("Agent launch results:", launch_result)
