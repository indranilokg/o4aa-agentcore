# AgentCore MCP Adapter

A demonstration of an AI agent deployed on AWS Bedrock AgentCore, authenticated via Okta, with a Flask web chat interface.

## Architecture

```
User → Flask App (App/) → AWS Bedrock AgentCore → Strands Agent (Agent/)
                ↑                                         ↑
           Okta OAuth2                             Okta MCP Adapter
```

- **App/** — Flask web application handling Okta OAuth2 login and chat UI
- **okta_sample_agentcore_with_xaa/** — Strands-based agent with Okta XAA (Cross-App Access), deployed to AWS Bedrock AgentCore

## Prerequisites

- Python 3.12+
- AWS account with Bedrock AgentCore access (us-east-1)
- Okta developer account with a configured authorization server
- AWS CLI configured with appropriate permissions

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd o4aa-agentcore
```

### 2. Configure environment variables

Copy the example env files and fill in your values (do not commit `.env`; it is in `.gitignore`):

```bash
cp App/.env.example App/.env
cp okta_sample_agentcore_with_xaa/.env.example okta_sample_agentcore_with_xaa/.env
```

### 3. Install dependencies

```bash
pip install -r App/requirements.txt
pip install -r okta_sample_agentcore_with_xaa/requirements.txt
```

## Deploying the Agent

```bash
cd okta_sample_agentcore_with_xaa
python agent_deployement.py
```

This will:
- Create an ECR repository for the agent container
- Create IAM execution roles
- Build and deploy the agent to Bedrock AgentCore via CodeBuild
- Output the `AGENT_RUNTIME_ARN` — copy this to `App/.env`

## Running the Web App

```bash
cd App
python app.py
```

Visit `http://127.0.0.1:8000` in your browser.

## Okta Configuration

In your Okta Admin Console, ensure the following is configured for your app:

- **Sign-in redirect URI**: `http://127.0.0.1:8000/callback`
- **Grant types**: Authorization Code
- **Sign-in redirect URI** must match `OKTA_CALLBACK_URL` in `App/.env`

## Environment Variables

See `App/.env.example` and `okta_sample_agentcore_with_xaa/.env.example` for all required variables.

## Before pushing to GitHub

- Do not commit **`.env`** (use `.env.example` as a template; `.env` is in `.gitignore`).
- Do not commit **`venv/`**, **`.bedrock_agentcore.yaml`**, or **`*.pem`** / **`*.key`** files.

## Documentation

- **[docs/](docs/)** — Architecture and sequence diagrams (Mermaid) and a **whitepaper** for securing AgentCore agents with Okta: two approaches (direct agent → XAA-protected MCP, and Gateway + Lambda interceptor). See [docs/README.md](docs/README.md) and [docs/whitepaper-secure-agentcore-okta-xaa.md](docs/whitepaper-secure-agentcore-okta-xaa.md).

## Project Structure

```
o4aa-agentcore/
├── docs/
│   ├── README.md
│   ├── whitepaper-secure-agentcore-okta-xaa.md
│   ├── 01-sequence-direct-xaa.md
│   ├── 02-sequence-gateway-interceptor.md
│   ├── 03-architecture-direct-xaa.md
│   └── 04-architecture-gateway-interceptor.md
├── App/
│   ├── app.py                  # Flask web application
│   ├── requirements.txt
│   ├── .env.example
│   ├── static/
│   └── templates/
├── okta_sample_agentcore_with_xaa/   # Strands agent with Okta XAA (direct MCP)
│   ├── agent.py
│   ├── agent_deployement.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── Dockerfile
│   └── README.md
└── README.md
```
