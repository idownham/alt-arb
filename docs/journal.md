# Project Osprey — Journal

Dated record for the class project: work, commits, observations/tests, decisions, AI use and Ian's judgment.
⭐ marks evidence → decision → consequence entries.

**About this file:** created 2026-10-28. Entries up to 2026-10-27 were **reconstructed only from the git history
and `docs/decisions.md` / `docs/plan.md`**. Nothing was added from memory. Where the record doesn't say something
(e.g. how long a task took, or what Ian thought beyond the recorded decision), this journal doesn't either.
From 2026-10-28 on, entries are written at the time.

Commit links: `https://github.com/idownham/alt-arb/commit/<hash>`.

**AI use, overall:** the code and docs were written with Claude Code. 52 of the 53 commits up to
2026-10-28 carry a `Co-Authored-By: Claude` trailer. Ian set the goals and the hard rules (`CLAUDE.md`),
pre-registered the Decision A criteria, and made each decision recorded below. Pair verification by an agent needs
Ian's explicit approval of each pair or rules template (rule 3).

---

## 2026-10-06 — Project start
- **Work:** repo created; `CLAUDE.md` (hard rules: read-only by default, secrets in `.env`, verified pairs only,
  edge net of fees and sized against depth) and the build plan (`b04ed56`, `1bb44db`).

## 2026-10-10 — Phase 1 + Phase 2 core built in one day
- **Work:** project skeleton (`5791199`); read-only Kalshi and Polymarket US clients (`a1e0c4e`, `cd16abc`);
  `pairs.csv` schema (`86cfec3`); shared 429 back-off and Retry-After handling (`71d3efe`, `52f29a5`); game-winner
  candidate matcher (`e3f0d21`, `9250a32`); fee models for both venues (`067d4e3`); depth-aware edge (`9c522ed`);
  scanner loop with SQLite log (`ffe9d54`); launchd service (`8c56770`); weekly pair refresh (`b0a1fa7`);
  cloud deploy scripts (`98b3ac3`).
- **Verification:** rule 3 extended so an agent may verify a pair only after Ian approves that specific pair in
  chat (`1d2bf8c`). First pair verified, Bears vs Packers (`0c0737e`), then NFL 54, NBA 76 and NHL 70 + 4 markets
  (`8df9fd4`, `0bb723d`, `7f01ee2`, `0e2650e`).
- **Decision:** WebSockets deferred to Phase 4, because both venues need KYC'd API keys even for public market
  data. REST scans are slower, a bias recorded for Decision A (`4577173`; the plan dates it 2026-10-11).

## 2026-10-13 — Clean data starts; Decision A pre-registered
- ⭐ **Evidence → decision → consequence (scanner fix):** the first opportunities compared books fetched minutes
  to hours apart (all Polymarket books first, Kalshi later). **Decision:** fetch both legs of a pair back to back
  and reject stale comparisons (`9537fff`). **Consequence:** rows logged before the fix
  (`leg_skew_seconds IS NULL`, including all 41 opportunities in the laptop DB) are treated as artifacts and excluded.
- **Work:** scanner moved to a DigitalOcean droplet (verified: full scan, auto-update, crash restart, reboot;
  laptop service uninstalled) (`d5c578c`). Opportunity persistence (`streak`, `first_seen`) (`8944d1a`).
- ⭐ **Decision A criteria pre-registered before any clean data** (`8944d1a`): GO ≥ 5 qualifying episodes/week,
  STOP < 1/week. A qualifying episode is pre-game, lasts ≥ 2 scans and peaks at ≥ $20 net.
- **Coverage:** 5 more leagues' rules templates approved, and only games resolving within 72 h are scanned
  (`42ed952`). 420 markets verified across 7 leagues (`910e987`). Daily auto-approval of template-checked games
  (`verified_by = Auto`) approved by Ian as an exception to rule 3 (`6aaa369`). Fix to template masking and the
  side check (`fac7a1e`).
- ⭐ **Scan time bias recorded** (`9e074fe`): with 8 leagues a scan grew from ~75 s to ~4.2 min, so short gaps are
  undercounted. Criteria unchanged; the bias is recorded, not acted on.
- ⭐ **Kalshi placeholder dates** (`f14d4c3`, `d01d5ec`): the refresh blocked 53 college football games because
  Kalshi listed Friday placeholder kickoffs. **Ian's decision:** approve the 34 Oct 17 games (ESPN confirmed the real
  times; `verified_by = Agent`, `DATE EXCEPTION` notes) and wait on the Oct 24 games. The date check stays strict.
- **Work:** `python -m altarb.status` health check (`fa7e8e2`). Kalshi's 0.07 fee base confirmed against the fee
  schedule PDF, which Ian fetched himself because it blocks automated download (`32b1407`).

## 2026-10-14 — Folder move; refresh running daily
- **Work:** laptop checkout moved to `~/code/alt/project-osprey` (HQ decision). `.venv` rebuilt, 165 tests pass
  (`267656a`). The daily refresh auto-verified 91 markets (`f10db02`).

## 2026-10-15 — Refresh rerouted; status false alarm; WAL
- ⭐ **Evidence → decision → consequence (refresh):** the 9 am refresh fired but no run session started and nothing
  was committed. The scheduled task's working folder still pointed at the old `~/code/alt-arb`, and only its prompt
  had been updated. **Decision (Ian):** recreate it from the new folder and disable the old one. The new task also reads the
  droplet address from `.env` instead of the prompt. **Consequence:** the test run started in the right folder and
  pushed 34 markets (`ae47958`).
- ⭐ **Evidence → decision → consequence (status):** HQ saw `altarb.status` report FAIL "no finished scan" while
  the scanner was fine. The droplet's `sqlite3` reads hit "database is locked". **Decision (Ian):** report an
  unreadable log as "couldn't read DB", with retries and tests (`569ba4e`). Live checks then showed the lock lasted
  most of each scan. **Decision (Ian):** switch the scan log to SQLite WAL mode (`ef072f8`). A test reproduces the
  lock under the old journal mode. **Consequence:** deployed by the normal pull, and status was HEALTHY.
- **Observation:** pairs grew to 327–338 and scans to ~8–13 min. Recorded as more Decision A bias, with criteria unchanged.

## 2026-10-16 → 2026-10-27 — Data collection
- **Daily refresh commits** (markets auto-verified): Oct 16: 32 (`869623d`) · Oct 17: 34 (`bc35cf5`) ·
  Oct 18: 146 (`8e4151a`) · Oct 20: 106 (`c87fffe`) · Oct 21: 14 (`fc293db`) · Oct 22: 6 (`c013f80`) ·
  Oct 23: 28 (`bd1f98e`) · Oct 24: 120 (`8dd6a02`) · Oct 25: none ready (run summary) · Oct 26: 8 (`bc2a4e0`) ·
  Oct 27: 70 (`9d8d69c`). No refresh commit on Oct 19.
- **Observations from the scan log (read 2026-10-28):** around Oct 22 07:00 UTC, about one hour of scans failed
  (34,333 pair errors). From Oct 25 the refresh blocked 245 markets as "Polymarket rules text differs from the
  approved template", so scans fell to 72–76 pairs on Oct 26–27.

## 2026-10-28 — Decision point A: STOP ⭐
- **Observation:** the 9 am refresh run failed (the laptop couldn't reach the API: ENOTFOUND). Status showed the scanner healthy.
- ⭐ **Evidence:** `altarb.report`: 15.2 days, 6,494 scans, 813,062 pair checks, 8 leagues. 1,040 opportunity
  episodes (1,032 in-play, 8 pre-game). **0 qualifying = 0.0/week.** Largest pre-game profit $2.33 (one scan). All
  487 logged opportunities ≥ $20 were in-play, and the largest looked like stale late-game books.
- ⭐ **Decision (Ian, in chat):** STOP, per the pre-registered rule. Phases 3–4 won't be built.
- **Consequence:** Osprey moves to close-out. Scanner and refresh keep running until Ian decides to shut them
  down. In HQ terms this frees a slot for Project Bloodhound. Biases (scan speed, coverage drop, Oct 22 outage) are
  recorded in `docs/decisions.md`; all of them point toward under-counting edge.
- **AI use + Ian's judgment:** Claude ran the report, queried the log for the pre-game episodes and error bursts,
  and drafted these entries. Ian accepted the STOP verdict without changing the criteria.
- **Not done yet:** Ian's first-hand spot-check of ~10 auto/agent-verified pairs.

## 2026-10-29 → 2026-11-08 — Scanner and refresh kept running after STOP (backfilled 2026-11-08)
*From the git log and the scheduled task's run history.*
- **Daily refresh commits** (markets auto-verified): Oct 29: 26 (`1b52b6b`) · Oct 30: 24 (`2dda98c`) ·
  Oct 31: 16 (`785d6cb`) · Nov 1: 184 (`e0b849d`) · Nov 2: 12 (`91010db`) · Nov 3: 74 (`cb8517c`) ·
  Nov 4: 20 (`4e32038`) · Nov 5: 10 (`b8591f8`) · Nov 6: 20 (`2fe75fb`) · Nov 7: 24 (`e9ac2fe`).
- **Nov 8:** the run committed nothing. Polymarket US dropped the connection twice.
- **Observation (2026-11-08 status):** the scanner was healthy but covered only ~36–42 pairs, because 142 markets sat in
  "needs review" ("Polymarket rules text differs from the approved template"). Templates weren't re-approved
  after STOP, since that would only matter if the project continued.
- **Open owner decision:** shut down the scanner and refresh, or keep logging.

## 2026-11-08 — Droplet resized for the shared recorders
- **Work:** Ian resized droplet `alt-arb` from Basic 1 vCPU / 1 GB / 25 GB ($6/mo) to **1 vCPU / 2 GB / 50 GB
  ($12/mo)**, with the disk included (permanent, can't shrink back). This follows the HQ decision of 2026-10-15
  to run all recorders on one server. Coin Toss and Slot Machine had been losing hours of data a day to laptop
  sleep (Slot Machine's gaps add up to ~3–20 h a day since Oct 31). Moving them is done separately, from an HQ session.
- **Test (after boot):** 2 GB RAM, `/dev/vda` 50 GB with the filesystem grown to 48 GB (44 GB free), the clock
  NTP-synchronized, the scan log still in WAL mode, the scanner and update timer active, and `altarb.status`
  scanner checks OK.
- **Observation:** scan-log gap ≈ 4 min. The last scan before the power-off finished 2026-11-09 04:24:27 UTC and
  the first after boot started 04:28:21 UTC and finished 04:32:00 (36 pairs, 0 errors). These are droplet clock
  times, and NTP wasn't synced yet at boot, so treat the gap as approximate.
- **AI use + Ian's judgment:** Claude walked through the resize steps and verified the server afterward. Ian chose
  the plan ($12, not $18: a second CPU isn't needed at load ≈ 0.02, and $18 adds only 10 GB of disk) and did the
  resize himself in the DigitalOcean console.
