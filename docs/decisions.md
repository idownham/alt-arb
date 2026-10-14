# alt-arb — Decision log

Notable decisions, trade-offs, and known biases, newest first. The roadmap lives in `docs/plan.md`.

## 2026-10-13 — Kalshi's Friday placeholder dates: approve Oct 17 games, wait on Oct 24

**What happened:** the daily refresh blocked 53 college football games (106 markets) because the
venues' rules texts name different dates: Kalshi says Friday (Oct 16 / Oct 23), Polymarket says Saturday
(Oct 17 / Oct 24). Kalshi gives every one of them the same Friday 8:00 PM ET kickoff, which is a placeholder.
ESPN confirms all 53 kick off on Saturday at Polymarket's time, to the minute, and ESPN's times are final.

**Decision (owner, in chat):**
- **Oct 17 (34 games, 68 markets): approved** with `verified_by = Agent` (commit `f14d4c3`), so they're
  scanned once they enter the 72h window. Each passed every other refresh check, re-run on fresh data.
  Each row's `resolution_notes` begins `DATE EXCEPTION`.
- **Oct 24 (19 games, 38 markets): wait.** If Kalshi corrects its dates, the daily refresh approves them
  as usual (`verified_by = Auto`). If not, revisit around Oct 22.

**Risk accepted:** in a normal game both venues settle on the real result (every kickoff is within Kalshi's
48h window). If a game is postponed, Kalshi's window runs from its Friday date, not the real kickoff,
so the two legs may settle differently.

**Not changed:** the refresh's date check stays strict. It caught a real data error; loosening it to allow
±1 day would also let through a genuinely different game (e.g. a back-to-back series).

## 2026-10-13 — Scan time grew to ~4 min; Decision A is more biased against short-lived gaps

**What changed:** as coverage expanded to 8 leagues (626 verified markets), a full REST scan grew from
~75s to ~4.2 min. That's with the 72h window (`HORIZON` in `src/altarb/scanner.py`), which limits each scan
to ~149 pairs / ~104 Polymarket markets. Without the window it would be longer. The main cost is
Polymarket's public rate limit (~30 req/min, then a 10s block), which the scanner paces at 2.5s/request.

**Why it matters:** a gap that opens and closes within one scan is never seen, and a qualifying episode
needs ≥ 2 consecutive scans, so it must last ~4 min at minimum and ~8 min to be reliably counted. Decision A therefore undercounts
short-lived edge more than it did at ~75s. Weigh this when reading a STOP or MARGINAL verdict.
The pre-registered criteria are unchanged; this records a known bias, not a new threshold.

**Mitigations:** keep the 72h window (don't widen it as leagues are added), and treat Phase 4's
WebSockets as the test for short-lived gaps.

## 2026-10-13 — The daily refresh pushes to `main` on its own

**What changed:** the scheduled "alt-arb daily pair refresh" (9am, Claude desktop app) commits
auto-verified pairs (`verified_by = Auto`) and pushes to `main` without a human in the loop.

**Rule:** `main` can move under you. Always `git pull --rebase` before every push, from the laptop and
any agent session alike, and never force-push `main`.

## 2026-10-14 — Laptop folder moved to `~/code/alt/project-osprey`

**What changed:** Ian moved this repo's laptop checkout from `~/code/alt-arb` to `~/code/alt/project-osprey`
(HQ decision, same date). The GitHub repo (`idownham/alt-arb`) and the droplet path (`/opt/alt-arb`) are unchanged.
The `.venv` was rebuilt (`pip install -e '.[dev]'`, 165 tests pass) and the daily-refresh scheduled task's prompt
now uses the new path.
