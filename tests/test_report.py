from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from altarb.pricing.edge import Opportunity
from altarb.report import build_report
from altarb.storage import Store

T0 = datetime(2026, 10, 14, tzinfo=timezone.utc)
LEAGUES = ["KXNFLGAME-A-X", "KXNBAGAME-B-Y", "KXNHLGAME-C-Z"]


def opp(ticker, profit):
    return Opportunity(
        kalshi_ticker=ticker, polymarket_slug="pm", kalshi_side="yes", polymarket_side="no", contracts=D(10),
        kalshi_fills=[], polymarket_fills=[], kalshi_cost=D(4), polymarket_cost=D(5), profit=D(profit),
        return_on_capital=D("0.1"), days_to_resolution=D(1), annualized_return=D(36),
    )


def build(tmp_path, days, episodes=(), leagues=LEAGUES):
    """A scan log spanning `days`, with one pair snapshot per league per scan, plus episodes:
    (ticker, start_day, scans, profit, in_play)."""
    store = Store(tmp_path / "s.db")
    scan_ids = []
    for day in (0, days):
        at = T0 + timedelta(days=day)
        sid = store.start_scan(at)
        for t in leagues:
            store.add_pair_snapshot(sid, at, t, "pm", D("1.01"), D("1.02"), 0.1)
        store.finish_scan(sid, at + timedelta(minutes=3), len(leagues), 0)
        scan_ids.append(sid)
    for ticker, start_day, scans, profit, in_play in episodes:
        first = T0 + timedelta(days=start_day)
        for n in range(1, scans + 1):
            store.add_opportunity(scan_ids[0], first + timedelta(minutes=3 * n), opp(ticker, profit), 0.1, in_play,
                                  None, n, first)
    store.commit()
    return build_report(store.conn)


def test_too_early_without_enough_days(tmp_path):
    r = build(tmp_path, days=3, episodes=[("KXNFLGAME-A-X", 1, 3, 50, False)] * 1)
    assert r.verdict == "TOO EARLY"


def test_too_early_without_enough_leagues(tmp_path):
    r = build(tmp_path, days=14, leagues=LEAGUES[:2])
    assert r.verdict == "TOO EARLY"


def test_go_when_enough_qualifying_episodes(tmp_path):
    # 11 qualifying episodes over ~14 days = 5.5/week -> GO
    eps = [(f"KXNFLGAME-A-{i}", i, 2, 25, False) for i in range(11)]
    r = build(tmp_path, days=14, episodes=eps)
    assert len(r.qualifying) == 11 and r.qualifying_per_week == pytest.approx(5.5, abs=0.01)
    assert r.verdict == "GO to Phase 3"


def test_stop_when_almost_nothing_qualifies(tmp_path):
    eps = [
        ("KXNFLGAME-A-1", 1, 1, 500, False),   # one scan only
        ("KXNFLGAME-A-2", 2, 5, 500, True),    # in-play
        ("KXNFLGAME-A-3", 3, 5, 5, False),     # too small
        ("KXNFLGAME-A-4", 4, 3, 30, False),    # the only qualifying one
    ]
    r = build(tmp_path, days=14, episodes=eps)
    assert [e.kalshi_ticker for e in r.qualifying] == ["KXNFLGAME-A-4"]
    assert r.verdict == "STOP (edge negligible)"


def test_marginal_in_between(tmp_path):
    eps = [(f"KXNFLGAME-A-{i}", i, 3, 40, False) for i in range(4)]  # 2/week
    r = build(tmp_path, days=14, episodes=eps)
    assert r.verdict.startswith("MARGINAL")


def test_episode_counted_once_with_its_longest_streak_and_peak(tmp_path):
    r = build(tmp_path, days=14, episodes=[("KXNFLGAME-A-1", 1, 4, 25, False)])
    [e] = r.episodes
    assert (e.scans, e.peak_profit) == (4, 25.0)


def test_pre_fix_rows_are_ignored(tmp_path):
    store = Store(tmp_path / "s.db")
    sid = store.start_scan(T0)
    store.add_opportunity(sid, T0, opp("KXNFLGAME-A-1", 94677))  # no skew: pre-fix row
    store.finish_scan(sid, T0, 1, 0)
    store.commit()
    assert build_report(store.conn).episodes == []


def test_older_database_without_streaks_reports_none(tmp_path):
    import sqlite3

    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE scans (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, pairs_checked INTEGER,
            errors INTEGER, skipped_stale INTEGER);
        CREATE TABLE pair_snapshots (scan_id INTEGER, ts TEXT, kalshi_ticker TEXT, polymarket_slug TEXT,
            kalshi_yes_cost TEXT, kalshi_no_cost TEXT, leg_skew_seconds REAL);
        CREATE TABLE opportunities (id INTEGER PRIMARY KEY, scan_id INTEGER, kalshi_ticker TEXT, profit TEXT,
            leg_skew_seconds REAL, in_play INTEGER);
    """)
    r = build_report(conn)
    assert r.episodes == [] and r.has_streaks is False and r.verdict == "TOO EARLY"
