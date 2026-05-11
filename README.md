# hyperliquid-quant-skill

> Formulaic short-term quant trading skill for OpenClaw / Claude Desktop / any MCP client. Trades on Hyperliquid via the official Python SDK with strict, rule-based discipline — no LLM discretion at the execution layer.

## What this skill does

Exposes 8 MCP tools that let an agent run a fully mechanical short-term strategy on Hyperliquid perpetuals:

| Tool | Purpose |
|---|---|
| `hl_get_account_state` | Read balance, positions, margin usage |
| `hl_get_market_data` | Mid price, funding, 24h volume, recent candles |
| `hl_compute_signals` | Funding Z-score + liquidation cluster proximity + EMA/ATR (formulaic) |
| `hl_evaluate_strategy` | Apply the full entry/exit formula → BUY / SELL / HOLD decision |
| `hl_risk_check` | L4 risk guard — daily loss cap, consecutive losses, margin %, news blackout |
| `hl_place_order` | Place a perp order with mandatory stop-loss and take-profit |
| `hl_cancel_order` | Cancel by oid |
| `hl_close_position` | Reduce-only market close |

The agent **must** call `hl_evaluate_strategy` and `hl_risk_check` before any `hl_place_order`. The risk guard refuses execution if any rule is violated.

## Design principles

1. **Formulaic, not discretionary** — every entry/exit is a deterministic function of market data. The LLM cannot override the rules.
2. **API Wallet only, never main wallet** — Hyperliquid sub-wallets cannot withdraw funds. If the bot is compromised, the worst case is bad trades, not stolen capital.
3. **Testnet first** — the skill defaults to testnet. You must explicitly set `HL_NETWORK=mainnet` to trade real funds.
4. **Two strikes, no third chance** — daily loss > 2% halts the bot until next UTC day. Three consecutive losses → 4-hour cool-down.

## Install

```bash
pip install hyperliquid-quant-skill
# or from source:
git clone https://github.com/YOUR_ORG/hyperliquid-quant-skill
cd hyperliquid-quant-skill
pip install -e .
```

## Configure

Copy `.env.example` to `.env` and fill in:

```
HL_API_WALLET_PRIVATE_KEY=0x...   # API wallet, NOT main wallet
HL_MAIN_ADDRESS=0x...             # Your main account address (read-only purpose)
HL_NETWORK=testnet                # or "mainnet" (only after testnet validation)
```

**How to get the API Wallet key:**
1. Go to https://app.hyperliquid.xyz → click your wallet → "API"
2. Click "Generate" — copy the private key shown ONCE
3. Set the address as authorized API wallet (trade-only, no withdraw)
4. Paste the private key into `.env`

## Register with OpenClaw

Add to `~/.openclaw/config.yaml`:

```yaml
skills:
  - name: hyperliquid-quant
    command: hl-quant-mcp
```

Then your OpenClaw agent can call all 8 tools.

## Register with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hyperliquid-quant": {
      "command": "hl-quant-mcp"
    }
  }
}
```

## The strategy in one diagram

```
  Market data (Hyperliquid WS/REST)
            │
            ▼
  Signals: funding Z-score, liquidation cluster, EMA/ATR
            │
            ▼
  Strategy formula (deterministic)
   ├─ Long  if F_z < -2.0 AND price near lower liq cluster AND EMA fast > slow × 0.998 AND last 15m candle reversal
   └─ Short if F_z > +2.0 AND price near upper liq cluster AND EMA fast < slow × 1.002 AND last 15m candle reversal
            │
            ▼
  Risk guards (any failure → reject)
   ├─ Daily PnL ≥ -2% of equity
   ├─ < 3 consecutive losses in last 4h
   ├─ Margin usage < 60%
   ├─ Symbol not already in position
   └─ News blackout window inactive
            │
            ▼
  Position sizing: notional = (E × 0.5%) / SL_dist × price
                   cap at 20% of equity, leverage ≤ 5x
            │
            ▼
  Execute on Hyperliquid (API Wallet signs)
```

## Safety red lines (cannot be disabled)

* API Wallet key only. The skill **refuses to load** if the key looks like a main wallet (i.e., if the address has > $10k unhedged spot/perp balance — only the API Wallet derives a separate address with no balance).
* Per-trade risk hard-capped at 1% of equity.
* Max 5x leverage.
* Mandatory stop-loss on every order; rejected if missing.
* Reduce-only exits — strategy never adds to losing positions.

## Project layout

```
hyperliquid-quant-skill/
├── SKILL.md                          # OpenClaw / Claude Desktop skill descriptor
├── README.md
├── LICENSE                           # MIT
├── .env.example
├── pyproject.toml
├── examples/
│   ├── openclaw_config.yaml
│   └── claude_desktop_config.json
├── src/hyperliquid_quant/
│   ├── __init__.py
│   ├── config.py                     # Env loader
│   ├── execution.py                  # Hyperliquid SDK wrapper
│   ├── signals.py                    # Funding / liquidation / TA signals
│   ├── strategy.py                   # The deterministic formula
│   ├── risk.py                       # L4 risk guards
│   └── mcp_server.py                 # MCP entrypoint (stdio)
└── tests/
    └── test_strategy.py
```

## Roadmap

* [ ] Phase 0 — testnet 200-order validation (current)
* [ ] Phase 1 — mainnet $200 → $500 → $2000 staged rollout
* [ ] Phase 2 — multi-symbol concurrent (BTC + ETH + SOL)
* [ ] Phase 3 — alternative formula slots (volatility breakout, basis arb)
* [ ] Phase 4 — auto-recalibration of thresholds via walk-forward backtest

## License

MIT. Use at your own risk. **This is not financial advice.** Past performance of any formula does not guarantee future results. You are responsible for any losses incurred while running this code.
