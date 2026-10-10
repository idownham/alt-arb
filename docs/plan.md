# alt-arb — Build Plan

## Goal
Find out, with real data, whether fillable net-of-fee arbitrage exists between Kalshi and Polymarket US,
and build the system to capture it safely if it does.

## Phase 1 — Scanner (weeks 1–2) · no money at risk
- [x] Project setup: `pyproject.toml`, `.venv`, `.gitignore` (incl. `.env`), pytest
- [x] Kalshi client: list markets, fetch order book (public data)
- [x] Polymarket US client: list markets, fetch order book (public API, no key)
- [x] `data/pairs.csv` schema: kalshi_ticker, polymarket_slug, inverted, description, resolution_notes, expiry, verified, verified_by, verified_on (see `docs/pairs.md`)
- [ ] Hand-verify 20–50 pairs (start with sports + major politics/econ — the most overlap)
- [ ] Candidate matcher: suggest likely pairs from titles/dates for human review

## Phase 2 — Edge calculation + data log (weeks 3–4)
- [ ] Fee models for both venues, with unit tests
- [ ] Depth-aware edge: walk both books, compute fillable size and net profit at each level
- [ ] Annualized return using time to resolution
- [ ] Scanner loop → SQLite: every snapshot + every opportunity above threshold
- [ ] Switch live books to WebSockets
- [ ] Run continuously (laptop first, then a small cloud server)

**Decision point A (end of week 4):** Review the log. How often do net-positive gaps appear, how big,
how deep, how long do they last? If edge is negligible, stop here — the system is still a finished project.

## Phase 3 — Paper trading (weeks 5–6)
- [ ] Simulated execution against live depth, including latency between legs
- [ ] Measure: both-legs-fill rate, slippage vs. detected edge, partial-fill frequency
- [ ] Define leg-risk policy (min fill, leg order, unwind rules)

**Decision point B:** Does paper P&L survive realistic fills? If yes → Phase 4.

## Phase 4 — Small live trading (weeks 7–8)
- [ ] KYC + API keys on both venues; pre-fund both accounts
- [ ] Live order module behind `LIVE_TRADING` flag with hard per-order and per-day caps
- [ ] Manual approval mode first, then automated within caps
- [ ] Position reconciliation across venues, P&L per pair, unhedged-exposure alerts, kill switch

## Phase 5 — Extensions (later)
- Crypto-price markets on both venues priced against options-implied probabilities (Deribit/CME)
- Skills to add once workflows repeat: `verify-market-pair`, `add-market-mapping`, `spread-report`

## Known risks
- **Resolution mismatch** — the biggest one. Identical-looking markets with different rules can lose both legs.
- Partial fills leaving one-sided exposure.
- Capital lockup until resolution.
- Fee changes (Kalshi multipliers vary by series; Polymarket varies by category).
- State-level legal status of prediction markets is in flux — check before trading live.
- Competition from faster bots on high-volume markets.
