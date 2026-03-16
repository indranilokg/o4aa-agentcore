# Documentation: Secure AgentCore Agents with Okta (XAA)

This folder contains architecture and sequence diagrams (Mermaid) and a whitepaper for two integration approaches that connect an AgentCore-hosted agent to an Okta Cross-App Access (XAA) protected MCP server.

## Whitepaper

**[whitepaper-secure-agentcore-okta-xaa.md](whitepaper-secure-agentcore-okta-xaa.md)** — Describes both approaches, includes inline Mermaid diagrams (1A, 2A, sequence 1, sequence 2), and highlights important code segments.

## Standalone diagrams

| File | Content |
|------|---------|
| [01-sequence-direct-xaa.md](01-sequence-direct-xaa.md) | **Sequence (1):** Agent → Okta XAA → MCP server (direct; no Gateway). |
| [02-sequence-gateway-interceptor.md](02-sequence-gateway-interceptor.md) | **Sequence (2):** Agent → Gateway → Lambda interceptor → **Okta MCP Adapter** (validates id token, performs XAA) → Target MCP. |
| [03-architecture-direct-xaa.md](03-architecture-direct-xaa.md) | **Architecture (1A):** Direct agent to XAA-protected MCP. |
| [04-architecture-gateway-interceptor.md](04-architecture-gateway-interceptor.md) | **Architecture (2A):** Gateway + Lambda interceptor → **Okta MCP Adapter** → Target MCP. Adapter architecture: [Okta MCP Adapter](https://github.com/indranilokg/okta-agent-mcp-adapter/blob/main/docs/ARCHITECTURE_DIAGRAM.md). |

All diagrams use [Mermaid](https://mermaid.js.org/) and render in GitHub, GitLab, VS Code (with a Mermaid extension), or any Mermaid-capable Markdown viewer.
