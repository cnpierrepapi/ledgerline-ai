# ledgerline

**The trust ledger for AI data agents.**

AI agents are writing to your data catalog right now: descriptions, tags, lineage notes, incident diagnoses. Ledgerline answers the question nobody else is asking: which of them can you actually trust?

Every action an agent takes against [DataHub](https://datahub.com) is recorded as a claim with a confidence score. When ground truth arrives (an assertion fires, an incident resolves, a steward accepts or reverts a change), the claim is settled. Settled claims accumulate into a per-agent calibration ledger: Brier scores, calibration curves, and a statistical verdict on whether an agent's track record is skill or luck.

The ledger is written back into DataHub itself, so the next agent inherits not just the metadata but the reliability of whoever wrote it.

## Components

- **Worker agents**: four scaffolded agents that do real catalog work through the DataHub MCP Server (impact forecasting, freshness prediction, enrichment, incident triage)
- **Settlement engine**: matches claims to observed outcomes and scores every agent
- **Trust gateway**: an MCP proxy in front of DataHub's MCP server that stamps every piece of context with its author's settled trust score. One URL swap for any MCP client.
- **Scoreboard**: live leaderboard with calibration curves and per-claim drill-down

## Status

Under active development for the DataHub Agent Hackathon. Setup instructions, examples, and demo walkthrough landing here as components ship.

## License

Apache 2.0
