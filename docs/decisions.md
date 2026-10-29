# alt-arb — Decision log

Notable decisions, trade-offs, and known biases, newest first. The roadmap lives in `docs/plan.md`.

## 2026-10-28 — Decision point A: STOP ⭐ evidence → decision → consequence

**Evidence** (`python -m altarb.report` on a copy of the droplet's scan log, 2026-10-28 ~23:00 UTC):
- Coverage: 15.2 days of post-fix scanning, 6,494 scans, 813,062 pair checks, 8 leagues. That meets the
  "enough data" rule (≥ 14 days, ≥ 3 leagues).
- Best hedge cost before fees, per pair check: 87.8% cost $1.00–1.03, 8.2% ≥ $1.03, 2.8% $0.97–1.00, and
  0.09% (746 checks) < $0.97.
- Opportunity episodes: 1,040. 1,032 were in-play and 8 pre-game.
- **Qualifying episodes (pre-game, ≥ 2 consecutive scans, peak ≥ $20): 0 = 0.0/week.** The pre-registered STOP line is < 1/week.
- Not a near miss: no pre-game opportunity reached $20 even for a single scan. The largest pre-game profit
  was $2.33 (NBA PHX @ OKC, Oct 26, one scan). Every one of the 487 logged opportunities ≥ $20 was in-play.
  In-play gaps don't count by design: with a multi-minute scan they can't be captured reliably, and the largest of
  them (> $17,000 on single scans) look like late-game books that had already moved, not real edge.

**Decision (owner, in chat, 2026-10-28):** Decision point A = **STOP**. The pre-registered rule applies as written.
Phases 3–4 (paper and live trading) won't be built. Per the plan, the scanner, fee/edge models, pair
verification and data log stand as the finished project. A well-evidenced "no edge" is a valid result.

**Known biases, all pointing toward under-counting edge** (recorded so the verdict is read honestly, not to reopen it):
- Scan speed: scans took ~1.5–9 min, so gaps shorter than a scan are invisible (see the 2026-10-13 and 2026-10-15
  entries). Only WebSockets (Phase 4, needs KYC'd keys) could test faster gaps. That's out of scope now.
- Coverage fell in the last days: from Oct 25 the daily refresh blocked 245 markets as "Polymarket rules text
  differs from the approved template", so scans covered only 72–76 pairs on Oct 26–27 (up to 361 earlier).
- Oct 22, ~07:00 UTC: about one hour of scans (32 scans, 34,333 pair errors) failed outright. The report excludes
  scans with no clean snapshots, which is why it shows 980 errors rather than the raw 34,359.

**Consequence:** Osprey moves to close-out. The scanner and the daily refresh keep running for now. Shutting them
down (and freeing the droplet for the other projects' recorders) is a separate owner decision. In HQ terms, an
"Osprey STOP" opens Project Bloodhound's slot.

## 2026-10-15 — 327 pairs, ~8–13 min scans; `altarb.status` misread a locked scan log as "no scans"

**What changed:** the scanned pair count grew to 327 (281 on Oct 14, 149 when the 72h window was set on
Oct 13) as the daily refresh added NBA, NHL and more college football. An HQ check of the droplet's journal
on Oct 15 (~11:10 CT) showed scans finishing every ~8 min, with single scans taking ~7.5–13 min, up from ~4.2 min.

**Why it matters for Decision A:** the Oct 13 entry's bias gets worse. A qualifying episode needs ≥ 2
consecutive scans, so a gap must now last roughly 15–25 min to be counted reliably. Read a STOP or MARGINAL
verdict with that in mind. The pre-registered criteria are unchanged. This records a known bias, not a new threshold.

**Status false alarm (fixed):** `altarb.status` reported FAIL "no finished scan in the log" / "0 in 0 scans"
while the scanner was fine. Its `sqlite3` reads on the droplet hit "database is locked" and the empty output
was parsed as "no scans". Now each read retries 3 times and a failure is shown as `FAIL Scan log  couldn't read DB: …`.

**Found while testing the fix:** the lock wasn't brief. Three live checks in a row all hit it. The scanner
commits only in `finish_scan`, so each scan is one long write transaction, and with SQLite's default rollback
journal a large transaction can block readers for most of the scan.
**Follow-up (owner approved, same day):** the scan log now uses WAL mode (`ef072f8`), so readers see the last
committed state and never wait for the writer. A test reproduces the lock under the old journal mode. Deployed by
the normal 15-min pull (scanner restarted on `ef072f8` at 16:49 UTC). The droplet then showed `journal_mode = wal`,
scans finishing every ~8 min (338 pairs, 0 errors), and `altarb.status` HEALTHY.

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
