# alt-arb

Finds cross-venue arbitrage between Kalshi and Polymarket US. Read-only for now: nothing places orders.
See `CLAUDE.md` for the rules and `docs/plan.md` for the roadmap.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pytest                 # offline tests
.venv/bin/pytest -m live         # also hit the real APIs
```

## Workflow

1. **Find candidate pairs:** `.venv/bin/python -m altarb.mapping.matcher --league KXNBAGAME` writes
   `data/candidates.csv` (omit `--league` for every mapped league).
2. **Verify them** into `data/pairs.csv` following `docs/pairs.md`.
3. **Scan:** `.venv/bin/python -m altarb.scanner` checks every verified pair every 90s and logs to
   `data/scanner.db`. Stop with Ctrl+C. `--once` runs a single scan; `--min-profit 1` only logs
   opportunities worth more than $1.

A scan takes ~75s for 28 games, because Polymarket's public API allows ~30 requests/minute.

### Running it continuously (macOS)

```bash
scripts/scanner-service.sh install     # start now and at every login; restarts within 60s if it crashes
scripts/scanner-service.sh status      # running? last scans, opportunities so far
scripts/scanner-service.sh logs        # follow data/scanner.log
scripts/scanner-service.sh uninstall   # stop and remove the service
```

It runs under `caffeinate -is`, so the Mac won't idle-sleep (or system-sleep while plugged in).
Closing the lid still sleeps the Mac; scans pause and resume on wake. After adding verified
pairs there's no need to restart: each scan reloads `data/pairs.csv`.

## Reading the log

`data/scanner.db` is SQLite (`sqlite3 data/scanner.db`). Money columns are text, so cast for math.

```sql
-- Scan health: incomplete scans have no finished_at
SELECT id, started_at, pairs_checked, errors FROM scans ORDER BY id DESC LIMIT 10;

-- Every opportunity found, best first
SELECT ts, kalshi_ticker, kalshi_side, polymarket_side, contracts, profit, annualized_return
FROM opportunities ORDER BY CAST(annualized_return AS REAL) DESC;

-- How close each pair has come (hedge cost before fees; fees add ~2-3.5c, so < ~0.965 is interesting)
SELECT kalshi_ticker, MIN(CAST(kalshi_yes_cost AS REAL)) AS best_yes, MIN(CAST(kalshi_no_cost AS REAL)) AS best_no
FROM pair_snapshots GROUP BY kalshi_ticker ORDER BY MIN(best_yes, best_no) LIMIT 20;
```
