"""Decision point A report: does the scanner's data meet the pre-registered criteria?

The criteria live in docs/plan.md and are mirrored in the constants below. They were set on
2026-10-13, before any clean data existed; don't change them after looking at results.

Only post-fix rows count (leg_skew_seconds IS NOT NULL; see storage.py).

    python -m altarb.report                       # data/scanner.db
    python -m altarb.report --db server-copy.db   # e.g. a copy from the cloud server
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from altarb.storage import DEFAULT_PATH

# Pre-registered 2026-10-13 (docs/plan.md, "Decision point A criteria").
MIN_DAYS = 14
MIN_LEAGUES = 3
MIN_STREAK = 2  # lasted at least two consecutive scans, so it was there long enough to act on
MIN_PEAK_PROFIT = 20.0  # dollars, net of fees, sized against depth
GO_PER_WEEK = 5.0
STOP_PER_WEEK = 1.0


@dataclass(frozen=True)
class Episode:
    """One run of consecutive scans where the same pair and direction stayed profitable."""
    kalshi_ticker: str
    kalshi_side: str
    first_seen: str
    scans: int
    peak_profit: float
    in_play: bool


@dataclass(frozen=True)
class Report:
    days: float
    scans: int
    pair_checks: int
    errors: int
    skipped_stale: int
    leagues: int
    hedge_cost_buckets: dict[str, int]
    episodes: list[Episode]
    has_streaks: bool = True

    @property
    def qualifying(self) -> list[Episode]:
        return [e for e in self.episodes
                if not e.in_play and e.scans >= MIN_STREAK and e.peak_profit >= MIN_PEAK_PROFIT]

    @property
    def qualifying_per_week(self) -> float:
        return len(self.qualifying) / (self.days / 7) if self.days else 0.0

    @property
    def enough_data(self) -> bool:
        return self.days >= MIN_DAYS and self.leagues >= MIN_LEAGUES

    @property
    def verdict(self) -> str:
        if not self.enough_data:
            return "TOO EARLY"
        if self.qualifying_per_week >= GO_PER_WEEK:
            return "GO to Phase 3"
        if self.qualifying_per_week < STOP_PER_WEEK:
            return "STOP (edge negligible)"
        return "MARGINAL (consider Phase 4 WebSockets to test faster gaps)"


def build_report(conn: sqlite3.Connection) -> Report:
    first, last, scans, checks, errors, stale = conn.execute(
        """SELECT MIN(started_at), MAX(finished_at), COUNT(*), COALESCE(SUM(pairs_checked), 0),
                  COALESCE(SUM(errors), 0), COALESCE(SUM(skipped_stale), 0)
           FROM scans WHERE finished_at IS NOT NULL
             AND id IN (SELECT scan_id FROM pair_snapshots WHERE leg_skew_seconds IS NOT NULL)"""
    ).fetchone()
    days = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds() / 86400 if first else 0.0
    leagues = conn.execute(
        """SELECT COUNT(DISTINCT substr(kalshi_ticker, 1, instr(kalshi_ticker, '-') - 1))
           FROM pair_snapshots WHERE leg_skew_seconds IS NOT NULL"""
    ).fetchone()[0]
    buckets = dict(conn.execute(
        """SELECT CASE WHEN c < 0.97 THEN 'a: < 0.97' WHEN c < 1.00 THEN 'b: 0.97-1.00'
                       WHEN c < 1.03 THEN 'c: 1.00-1.03' ELSE 'd: >= 1.03' END, COUNT(*)
           FROM (SELECT MIN(CAST(kalshi_yes_cost AS REAL), CAST(kalshi_no_cost AS REAL)) AS c
                 FROM pair_snapshots WHERE leg_skew_seconds IS NOT NULL AND kalshi_yes_cost IS NOT NULL
                   AND kalshi_no_cost IS NOT NULL)
           GROUP BY 1 ORDER BY 1"""
    ).fetchall())
    # Read-only, so an older database (before streaks were recorded) isn't upgraded here: report no episodes.
    has_streaks = "streak" in {row[1] for row in conn.execute("PRAGMA table_info(opportunities)")}
    episodes = [Episode(t, side, first_seen, scans_, peak, bool(in_play)) for t, side, first_seen, scans_, peak, in_play in conn.execute(
        """SELECT kalshi_ticker, kalshi_side, first_seen, MAX(streak), MAX(CAST(profit AS REAL)), MAX(in_play)
           FROM opportunities WHERE leg_skew_seconds IS NOT NULL AND streak IS NOT NULL
           GROUP BY kalshi_ticker, polymarket_slug, kalshi_side, first_seen"""
    )] if has_streaks else []
    return Report(days, scans, checks, errors, stale, leagues, buckets, episodes, has_streaks)


def render(r: Report) -> str:
    lines = [
        f"Coverage: {r.days:.1f} days, {r.scans} scans, {r.pair_checks} pair checks, {r.leagues} leagues "
        f"({r.errors} errors, {r.skipped_stale} skipped as stale)",
        "Best hedge cost before fees, per pair check (fees add ~2.5-3.5c near 50/50):",
        *(f"  {k:14} {v}" for k, v in r.hedge_cost_buckets.items()),
        *([] if r.has_streaks else ["(This database predates streak tracking: episodes start being counted once the scanner is updated.)"]),
        f"Opportunity episodes: {len(r.episodes)} "
        f"({sum(e.in_play for e in r.episodes)} in-play, {sum(not e.in_play for e in r.episodes)} pre-game)",
        f"Qualifying (pre-game, >= {MIN_STREAK} consecutive scans, peak >= ${MIN_PEAK_PROFIT:.0f}): "
        f"{len(r.qualifying)} = {r.qualifying_per_week:.1f}/week",
    ]
    for e in sorted(r.qualifying, key=lambda e: -e.peak_profit)[:10]:
        lines.append(f"  {e.first_seen[:16]}  {e.kalshi_ticker} K-{e.kalshi_side}: {e.scans} scans, peak ${e.peak_profit:.2f}")
    lines += [
        "",
        f"Criteria: >= {MIN_DAYS} days and >= {MIN_LEAGUES} leagues of data; GO at >= {GO_PER_WEEK:.0f}/week, "
        f"STOP below {STOP_PER_WEEK:.0f}/week",
        f"VERDICT: {r.verdict}",
    ]
    if not r.enough_data:
        lines.append(f"  (need {max(0.0, MIN_DAYS - r.days):.1f} more days"
                     + (f" and {MIN_LEAGUES - r.leagues} more leagues" if r.leagues < MIN_LEAGUES else "") + ")")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Decision point A report from the scanner log.")
    parser.add_argument("--db", default=str(DEFAULT_PATH), help="SQLite path (default data/scanner.db)")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)  # read-only: safe on a live database
    print(render(build_report(conn)))


if __name__ == "__main__":
    main()
