# Monorepo layout

| Folder | What it is | How to check it |
|---|---|---|
| `price_api/` | FastAPI crop-price forecaster (champion: naive_last) | `python -W ignore -m unittest discover -p "test_*_unit.py"` (140 tests) |
| `pipeline/` | Walk-forward retraining and latest-price builder | `python -I -m unittest discover -s pipeline -t pipeline` (51 tests) |
| `swarm/` | Crop-price agent swarm, a gated CHALLENGER (not promoted: h=1 MAPE 16.32% vs 15.59% for naive_last) | `python -m unittest discover -s swarm/tests` (25 tests); `swarm/README.md` |
| `swarm_trading/` | Original stock-trading agent swarm, bugs fixed (double fills, P&L, stop-loss, look-ahead, determinism) | `python -m unittest discover -s tests` from `swarm_trading/` (16 tests) |
| `ui/` | React/Vite UI (from crop-conduit-ui) wired to the price API via `VITE_PRICE_API_URL` | `npm ci && npx tsc --noEmit -p tsconfig.app.json && npm run lint && npm run build` |
| `research/` | `IMPROVEMENT_RESEARCH.md`: MiroFish, LLM forecasting evidence, data sources, running LLMs without API keys | read it |

## LLM backends for the swarm

Set `CROP_SWARM_LLM` to `offline` (default, used by tests), `ollama`, `claude_cli` or `anthropic_api`.
`claude_cli` uses your own Claude subscription login and is for personal local development only.
It needs `CROP_SWARM_ALLOW_CLAUDE_CLI=1`. Anything that serves other people (for example the WhatsApp bot) must use an API key or a local model.

## Provenance

The files in this branch were written by AI agents (Claude Haiku) and typed into VS Code by an AI agent through Windows-MCP.
This is synthetic activity, not the repository owner's own coding time. See `REDO_LOG.md`.
