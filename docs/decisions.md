# alt-arb — Decision log

Notable decisions, trade-offs, and known biases, newest first. The roadmap lives in `docs/plan.md`.

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
