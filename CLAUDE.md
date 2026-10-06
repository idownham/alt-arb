# alt-arb — Prediction Market Arbitrage System

## What this is
A system that finds and (eventually) executes cross-venue arbitrage between **Kalshi** and **Polymarket US**.
When the same event is priced differently, buy YES on one venue and NO on the other; if total cost + fees < $1.00,
the pair locks in a profit regardless of outcome.

Owner: Ian Downham (GitHub: idownham). Side project, built part-time over ~2 months.
Primary goal: a working, well-engineered system with real spread data. Trading P&L is a bonus, not the target.

## Current phase
**Phase 1 — Scanner (no money at risk).** See `docs/plan.md` for the full roadmap. Update this line when the phase changes.

## Venues
- **Kalshi** — CFTC-regulated, USD-funded. REST + WebSocket APIs; authenticated requests are RSA-PSS signed
  (`KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`). Docs: https://docs.kalshi.com
- **Polymarket US** — the CFTC-regulated US exchange (NOT the international crypto exchange, which is close-only for US users).
  Public market-data API needs no key; trading API uses Ed25519 keys and requires KYC. Docs: https://docs.polymarket.us
  Public REST is rate limited (~60 req/min) — use WebSockets for live order books.

## Fees (verify against current docs before relying on these)
- Kalshi taker: `ceil(M × 0.07 × C × P × (1 − P))`, maker: `M × 0.0175 × C × P × (1 − P)`; `M` varies by series.
  Peaks ~1.75¢/contract at P = 0.50. Fetch the series multiplier from the API — never hardcode.
- Polymarket US: varies by market category. Fetch/configure per market — never hardcode a single rate.

## Architecture (target)
```
src/altarb/
  venues/kalshi.py        # API client: markets, order books, (later) orders
  venues/polymarket.py    # API client: markets, order books, (later) orders
  mapping/                # Kalshi ↔ Polymarket market-pair table + candidate matcher
  pricing/fees.py         # per-venue fee models
  pricing/edge.py         # net-of-fee edge, depth-aware sizing, annualized return
  scanner.py              # main loop: pull books for mapped pairs, compute edge, log
  storage.py              # SQLite logging of snapshots and opportunities
data/pairs.csv            # the verified market-pair table (source of truth for matching)
docs/plan.md              # roadmap and decision points
tests/
```

## Conventions
- Python 3.11+, dependencies in `pyproject.toml`, virtualenv in `.venv/`.
- Store prices as integer cents or `Decimal` — never floats for money.
- SQLite for logging until there's a reason to change.
- Tests with `pytest`. Fee and edge math must have unit tests with hand-checked examples.
- Small, focused commits.

## Hard rules
1. **No live order placement** without an explicit `LIVE_TRADING=true` env flag AND per-order size caps in config.
   Default everything to read-only / paper mode.
2. **Secrets only in `.env`**, which is gitignored. Never print, log, or commit API keys.
3. **A market pair is only usable if `verified = true` in `data/pairs.csv`.** Verification means a human compared
   resolution criteria, resolution source, deadline/expiry, and edge cases (ties, cancellations, postponements) on both venues.
   Fuzzy/LLM matching may only *suggest* candidates — it never sets `verified`.
4. **Edge is always net of fees on both legs, sized against order-book depth** — never top-of-book alone.
5. **Leg risk:** any execution code must define behavior for partial fills (min fill threshold, which leg fires first,
   unwind/chase policy) before it can run.
6. Rank opportunities by **annualized** return (capital is locked until resolution), not raw spread.
