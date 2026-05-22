import boto3, json
client = boto3.client('bedrock-agentcore-control', region_name='us-east-2')

response = client.update_gateway(
    gatewayIdentifier='hr-mcp-xaa-sample-nk95xscgqd',
    name='hr-mcp-xaa-sample',
    roleArn='arn:aws:iam::460828077711:role/service-role/AmazonBedrockAgentCoreGatewayDefaultServiceRole1773411835752',
    protocolType='MCP',
    authorizerType='CUSTOM_JWT',
    authorizerConfiguration={
        'customJWTAuthorizer': {
            'discoveryUrl': 'https://ijtestcustom.oktapreview.com/.well-known/openid-configuration',
            'allowedAudience': ['0oaw5jnp1nVVc1gJt1d7'],
        },
    },
    protocolConfiguration={
        'mcp': {
            'supportedVersions': ['2025-06-18'],
            'streamingConfiguration': {
                'enableResponseStreaming': True
            }
        }
    },
    interceptorConfigurations=[
        {
            'interceptor': {
                'lambda': {'arn': 'arn:aws:lambda:us-east-2:460828077711:function:agentcore-gateway-xaa-interceptor'}
            },
            'interceptionPoints': ['REQUEST'],
            'inputConfiguration': {'passRequestHeaders': True},
        },
    ],
)
print(json.dumps(response, indent=2, default=str))
