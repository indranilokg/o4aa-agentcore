import boto3, json

iam = boto3.client('iam')
role_name = 'AmazonBedrockAgentCoreGatewayDefaultServiceRole1773411835752'

# List inline policies
inline = iam.list_role_policies(RoleName=role_name)
print("Inline policies:", inline['PolicyNames'])
for p in inline['PolicyNames']:
    doc = iam.get_role_policy(RoleName=role_name, PolicyName=p)
    print(f"\n--- {p} ---")
    print(json.dumps(doc['PolicyDocument'], indent=2))

# List managed policies
attached = iam.list_attached_role_policies(RoleName=role_name)
print("\nManaged policies:")
for p in attached['AttachedPolicies']:
    print(f"  {p['PolicyName']} -> {p['PolicyArn']}")

# Check trust policy + permissions boundary
role = iam.get_role(RoleName=role_name)
print("\nTrust policy:")
print(json.dumps(role['Role']['AssumeRolePolicyDocument'], indent=2))
if 'PermissionsBoundary' in role['Role']:
    print("\nPermissions boundary:", role['Role']['PermissionsBoundary']['PermissionsBoundaryArn'])
