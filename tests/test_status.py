import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from altarb import status
from altarb.mapping.pairs import MarketPair
from altarb.mapping.refresh import Review, record_apply, write_run_record

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)

HEALTHY_OUTPUT = """service=active
last_scan=2026-10-14T14:56:00+00:00|149|0
last_hour=14|0
disk=9%|23068672
head=fac7a1e0000000000000000000000000000000000
"""


def test_parse_server():
    s = status.parse_server(HEALTHY_OUTPUT)
    assert s.service == "active"
    assert s.last_scan_at == datetime(2026, 10, 14, 14, 56, tzinfo=timezone.utc)
    assert (s.last_scan_pairs, s.scans_last_hour, s.errors_last_hour) == (149, 14, 0)
    assert (s.disk_used_pct, s.disk_free_kb) == (9, 23068672)
    assert s.head.startswith("fac7a1e")


def test_parse_server_with_empty_log():
    s = status.parse_server("service=active\nlast_scan=\nlast_hour=0|0\ndisk=9%|1\nhead=\n")
    assert s.last_scan_at is None and s.errors_last_hour == 0


def test_healthy_server_passes():
    checks = status.server_checks(status.parse_server(HEALTHY_OUTPUT), NOW)
    assert all(c.ok for c in checks)
    assert "4 min ago, 149 pairs" in checks[1].detail
    assert "22.0 GB free" in checks[3].detail  # 23068672 KB / 1024 / 1024


def test_each_server_problem_fails_its_check():
    s = status.parse_server(HEALTHY_OUTPUT)
    bad = {
        "Scanner service": s.__class__(**{**s.__dict__, "service": "failed"}),
        "Last scan": s.__class__(**{**s.__dict__, "last_scan_at": NOW - timedelta(minutes=16)}),
        "Errors (last hour)": s.__class__(**{**s.__dict__, "errors_last_hour": 1}),
        "Disk": s.__class__(**{**s.__dict__, "disk_used_pct": 90}),
    }
    for name, server in bad.items():
        failed = [c.name for c in status.server_checks(server, NOW) if not c.ok]
        assert failed == [name]


def test_scan_age_boundary():
    s = status.parse_server(HEALTHY_OUTPUT)
    at_limit = s.__class__(**{**s.__dict__, "last_scan_at": NOW - timedelta(minutes=15)})
    assert all(c.ok for c in status.server_checks(at_limit, NOW))


def record(ready=0, hours_ago=6, review=(), applied=None):
    return {"run_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "ready": ready,
            "ready_by_league": {"KXNHLGAME": ready} if ready else {}, "review": list(review), "applied": applied}


def no_push(since):
    return None


def test_refresh_nothing_ready_is_healthy():
    checks = status.refresh_checks(record(), NOW, "", find_push=no_push)
    assert all(c.ok for c in checks)
    assert checks[1].detail == "nothing to approve, so nothing to push"


def test_refresh_too_old_or_missing():
    assert not status.refresh_checks(record(hours_ago=27), NOW, "", find_push=no_push)[0].ok
    assert not status.refresh_checks(None, NOW, "")[0].ok


def test_refresh_ready_but_not_pushed_fails():
    checks = status.refresh_checks(record(ready=4, applied={"at": NOW.isoformat(), "markets": 4}), NOW, "",
                                   find_push=no_push)
    assert not checks[1].ok and "approved locally but no push found" in checks[1].detail


def test_refresh_pushed_and_deployed():
    checks = status.refresh_checks(record(ready=4), NOW, "abc", find_push=lambda since: "1234567890",
                                   is_deployed=lambda commit, head: True)
    assert checks[1].ok and checks[1].detail == "1234567, deployed on the droplet"
    assert "4 ready (KXNHLGAME 4)" in checks[0].detail


def test_review_reasons_drop_per_game_details():
    review = [
        {"kalshi_ticker": "KXNCAAFGAME-26OCT23MOHCMU-MOH", "polymarket_slug": "s",
         "reason": "matcher flagged it: AUTO: dates differ by 1 day; rules dates differ: Kalshi 2026-10-23, Polymarket 2026-10-24"},
        {"kalshi_ticker": "KXNCAAFGAME-26OCT23MOHCMU-CMU", "polymarket_slug": "s",
         "reason": "can't place Kalshi YES ('Central Michigan') on exactly one Polymarket side"},
    ]
    assert status.review_reasons(review) == {
        "matcher flagged it": 1, "rules dates differ": 1, "can't place Kalshi YES on exactly one Polymarket side": 1}
    text = status.render([status.Check("Disk", True, "ok")], review)
    assert "2 markets in 1 games" in text and text.endswith("HEALTHY")


def test_render_names_failed_checks():
    text = status.render([status.Check("Disk", False, "95% used"), status.Check("Pushed?", True, "x")], [])
    assert text.endswith("NEEDS ATTENTION: Disk")


def test_load_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\n\nexport DROPLET_HOST='root@203.0.113.7'\nOTHER=a=b\n")
    assert status.load_env(env) == {"DROPLET_HOST": "root@203.0.113.7", "OTHER": "a=b"}
    assert status.load_env(tmp_path / "missing") == {}


def test_droplet_host_prefers_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DROPLET_HOST=root@203.0.113.7\n")
    monkeypatch.delenv("DROPLET_HOST", raising=False)
    assert status.droplet_host(env) == "root@203.0.113.7"
    monkeypatch.setenv("DROPLET_HOST", "root@198.51.100.1")
    assert status.droplet_host(env) == "root@198.51.100.1"


def test_fetch_server_raises_on_ssh_failure():
    def fail(*args, **kwargs):
        return SimpleNamespace(returncode=255, stdout="", stderr="Connection timed out")
    try:
        status.fetch_server("root@203.0.113.7", run=fail)
    except RuntimeError as e:
        assert "Connection timed out" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_refresh_writes_and_updates_run_record(tmp_path):
    path = tmp_path / "refresh_last.json"
    ready = [MarketPair("KXNHLGAME-26OCT15BOSSJ-BOS", "aec-nhl-bos-sj-2026-10-15", False, "", "", None, False, "", None)]
    write_run_record(ready, [Review("K-1", "s", "why")], NOW, path)
    record_apply(1, NOW + timedelta(minutes=1), path)
    saved = json.loads(path.read_text())
    assert saved["ready"] == 1 and saved["ready_by_league"] == {"KXNHLGAME": 1}
    assert saved["review"] == [{"kalshi_ticker": "K-1", "polymarket_slug": "s", "reason": "why"}]
    assert saved["applied"]["markets"] == 1


LOCKED_OUTPUT = """service=active
db_error=Error: in prepare, database is locked (5)
disk=9%|23068672
head=fac7a1e0000000000000000000000000000000000
"""


def test_unreadable_db_is_not_reported_as_no_scans():
    s = status.parse_server(LOCKED_OUTPUT)
    assert s.db_error == "Error: in prepare, database is locked (5)"
    checks = status.server_checks(s, NOW)
    assert [c.name for c in checks if not c.ok] == ["Scan log"]
    assert checks[1].detail == "couldn't read DB: Error: in prepare, database is locked (5)"
    assert not any("no finished scan" in c.detail or "0 in 0 scans" in c.detail for c in checks)


def run_remote_script(db, tmp_path):
    """REMOTE_SCRIPT run locally against `db`, with short lock waits so the test stays fast."""
    script = (status.REMOTE_SCRIPT.replace(status.DB, str(db))
              .replace(".timeout 5000", ".timeout 100").replace("sleep 2", "sleep 0"))
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, cwd=tmp_path)
    return status.parse_server(out.stdout)


def make_scan_log(db):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,"
                " pairs_checked INTEGER, errors INTEGER)")
    con.execute("INSERT INTO scans VALUES (1, '2026-10-15T16:00:00+00:00', '2026-10-15T16:03:44+00:00', 327, 0)")
    con.commit()
    return con


@pytest.mark.skipif(not shutil.which("sqlite3"), reason="needs the sqlite3 CLI")
def test_remote_script_reads_scan_log(tmp_path):
    db = tmp_path / "scanner.db"
    make_scan_log(db).close()
    s = run_remote_script(db, tmp_path)
    assert s.db_error == ""
    assert s.last_scan_at == datetime(2026, 10, 15, 16, 3, 44, tzinfo=timezone.utc) and s.last_scan_pairs == 327


@pytest.mark.skipif(not shutil.which("sqlite3"), reason="needs the sqlite3 CLI")
def test_remote_script_reports_a_locked_db(tmp_path):
    db = tmp_path / "scanner.db"
    con = make_scan_log(db)
    con.execute("BEGIN EXCLUSIVE")  # what a writer holds while it commits; readers get "database is locked"
    try:
        s = run_remote_script(db, tmp_path)
    finally:
        con.rollback()
        con.close()
    assert "locked" in s.db_error
    assert s.last_scan_at is None
