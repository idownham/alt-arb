# alt-arb — Build Plan

## Goal
Find out, with real data, whether fillable net-of-fee arbitrage exists between Kalshi and Polymarket US,
and build the system to capture it safely if it does.

## Phase 1 — Scanner (weeks 1–2) · no money at risk
- [x] Project setup: `pyproject.toml`, `.venv`, `.gitignore` (incl. `.env`), pytest
- [x] Kalshi client: list markets, fetch order book (public data)
- [x] Polymarket US client: list markets, fetch order book (public API, no key)
- [x] `data/pairs.csv` schema: kalshi_ticker, polymarket_slug, inverted, description, resolution_notes, expiry, verified, verified_by, verified_on (see `docs/pairs.md`)
- [x] Hand-verify 20–50 pairs (start with sports + major politics/econ — the most overlap) — 626 markets across 8 leagues by 2026-10-13 (NFL, NBA, NHL, college football, MLB, Champions League, Premier League, AFC Champions League); kept current by a daily auto-refresh (`verified_by = Auto`). Politics/econ not yet (Polymarket US lists no Trump "mention" markets)
- [x] Candidate matcher: suggest likely pairs from titles/dates for human review (game winners; see `docs/pairs.md`)
- [ ] Candidate matcher for non-game markets (politics, econ)

## Phase 2 — Edge calculation + data log (weeks 3–4)
- [x] Fee models for both venues, with unit tests (Kalshi 0.07 base confirmed against the fee schedule PDF, 2026-10-13)
- [x] Depth-aware edge: walk both books, compute fillable size and net profit at each level
- [x] Annualized return using time to resolution (simple, days floored at 1)
- [x] Scanner loop → SQLite: every snapshot + every opportunity above threshold (`python -m altarb.scanner`; REST, ~75s/scan)
- [ ] Switch live books to WebSockets — **deferred to Phase 4 (2026-10-11):** both venues' WebSockets need
      API keys (KYC'd accounts), even for public market data. Until then REST scans take ~75s, so gaps that
      open and close faster than that can be missed; keep that bias in mind at Decision point A.
- [x] Run continuously on the laptop (`scripts/scanner-service.sh`, launchd)
- [x] Run continuously on a small cloud server (so lid-closed / travel doesn't leave gaps) — DigitalOcean
      droplet `alt-arb` (Ubuntu 24.04) since 2026-10-13, set up with `deploy/setup.sh` (see `docs/deploy.md`).
      Verified: full scan (144 pairs, 0 errors, max leg skew 0.26s), auto-update, crash restart, reboot.
      Laptop service uninstalled.

- [x] Opportunity persistence: each opportunity records `streak` (consecutive scans) and `first_seen`
- [x] Decision point A criteria pre-registered (below) + `python -m altarb.report`
- [x] Scan only games resolving within 72h: 149 pairs / ~104 Polymarket markets, 4.2 min per scan, max leg skew 0.22s (2026-10-13)
- [x] Daily pair refresh scheduled (Claude desktop app, 9am): auto-verifies template-checked games, reports needs-review + report verdict

**Data caveat (2026-10-13):** opportunities logged before the scanner fix (rows with
`leg_skew_seconds IS NULL`, incl. all 41 in the laptop's `data/scanner.db`) compared books fetched minutes to
hours apart. They are artifacts, not edge: exclude them.

**Decision point A (end of week 4):** Review the log. How often do net-positive gaps appear, how big,
how deep, how long do they last? If edge is negligible, stop here — the system is still a finished project.

**Decision point A criteria** — pre-registered 2026-10-13, before any clean data. Do not change them after
looking at results; if they turn out to be wrong, say so explicitly and record the reason here.
Run `python -m altarb.report` (mirrors these numbers in `src/altarb/report.py`).

- **Enough data:** ≥ 14 days of post-fix scanning (`leg_skew_seconds IS NOT NULL`) across ≥ 3 leagues.
- **A qualifying opportunity** is an *episode* (consecutive scans where the same pair + direction stays
  profitable) that is **pre-game**, lasts **≥ 2 consecutive scans**, and peaks at **≥ $20** net profit
  (net of both venues' fees, sized against depth).
- **GO to Phase 3** if qualifying episodes average **≥ 5 per week**.
- **STOP** if they average **< 1 per week**: the edge is negligible for this scanner.
- **MARGINAL** in between: the cheapest further test is Phase 4's WebSockets (catches gaps shorter than a scan).
- In-play gaps are reported but don't count toward GO: with a multi-minute scan they can't be captured reliably.

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
