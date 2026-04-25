# Careloop — LA Hacks 2026

Built on [Fetch.ai](https://fetch.ai) Agentverse for LA Hacks 2026.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in AGENTVERSE_API_KEY, ANTHROPIC_API_KEY
```

## Run the agent

```bash
cd agents
python main.py
```

## MCP Servers (Claude Code)

Both Agentverse MCP servers are pre-configured:
- `agentverse-full` — `https://mcp.agentverse.ai/sse`
- `agentverse-lite` — `https://mcp-lite.agentverse.ai/mcp`

## Tracks

- **Track 1**: Agentverse Search & Discovery — agents must implement Chat Protocol
- **Track 2**: OmegaClaw Skill Forge — specialist skills via Agentverse

Promo code for ASI:One Pro + Agentverse Premium: `LAHACKSLAHACKSAV`
