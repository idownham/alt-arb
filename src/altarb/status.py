"""Health check: the cloud scanner (over SSH) and the last daily pair refresh.

    python -m altarb.status        # exit 0 = healthy, 1 = needs attention

The droplet address comes from DROPLET_HOST (environment, else .env), e.g. root@203.0.113.7.
Everything is read-only: one SSH call that reads the service state, the scan log and disk usage.

The refresh runs on the laptop, not the droplet, so its result comes from data/refresh_last.json
(written by `python -m altarb.mapping.refresh`) and "pushed?" from git.

Needs attention (exit 1) when: SSH fails, the scanner isn't active, the last finished scan is older
than MAX_SCAN_AGE, any scan errors in the last hour, the disk is DISK_FULL_PCT full, the last refresh
is older than MAX_REFRESH_AGE (or never ran), or it approved games that never reached GitHub.
"Needs review" games are listed but don't fail the check: some (e.g. a venue's wrong date) can stay
blocked for days by design.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from altarb.mapping.refresh import RUN_RECORD_PATH

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"

MAX_SCAN_AGE = timedelta(minutes=15)  # a scan takes ~4 min (docs/decisions.md)
MAX_REFRESH_AGE = timedelta(hours=26)  # daily at 9am, plus slack for a late laptop
DISK_FULL_PCT = 90

DB = "/opt/alt-arb/data/scanner.db"
REMOTE_SCRIPT = f"""
echo "service=$(systemctl is-active altarb-scanner)"
echo "last_scan=$(sqlite3 -readonly -cmd '.timeout 5000' {DB} \\
  "SELECT finished_at || '|' || pairs_checked || '|' || errors FROM scans
   WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1;")"
echo "last_hour=$(sqlite3 -readonly -cmd '.timeout 5000' {DB} \\
  "SELECT COUNT(*) || '|' || COALESCE(SUM(errors), 0) FROM scans
   WHERE finished_at IS NOT NULL AND julianday('now') - julianday(started_at) <= 1.0 / 24;")"
echo "disk=$(df -P / | tail -1 | awk '{{print $5 "|" $4}}')"
echo "head=$(runuser -u altarb -- git -C /opt/alt-arb rev-parse HEAD 2>/dev/null)"
"""


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Server:
    service: str
    last_scan_at: datetime | None
    last_scan_pairs: int
    scans_last_hour: int
    errors_last_hour: int
    disk_used_pct: int
    disk_free_kb: int
    head: str


# --- reading ------------------------------------------------------------------


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """KEY=VALUE lines; '#' comments, blank lines, `export ` and surrounding quotes are allowed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    env = {}
    for line in lines:
        line = line.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def droplet_host(env_path: Path = ENV_PATH) -> str | None:
    return os.environ.get("DROPLET_HOST") or load_env(env_path).get("DROPLET_HOST")


def parse_server(text: str) -> Server:
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    scan = fields.get("last_scan", "").split("|")
    hour = fields.get("last_hour", "").split("|")
    disk = fields.get("disk", "").split("|")
    return Server(
        service=fields.get("service", "unknown"),
        last_scan_at=datetime.fromisoformat(scan[0]) if scan[0] else None,
        last_scan_pairs=int(scan[1]) if len(scan) > 1 else 0,
        scans_last_hour=int(hour[0]) if hour[0] else 0,
        errors_last_hour=int(hour[1]) if len(hour) > 1 else 0,
        disk_used_pct=int(disk[0].rstrip("%")) if disk[0] else 0,
        disk_free_kb=int(disk[1]) if len(disk) > 1 else 0,
        head=fields.get("head", ""),
    )


def fetch_server(host: str, run: Callable = subprocess.run) -> Server:
    result = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, REMOTE_SCRIPT],
                 capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ssh exited {result.returncode}")
    return parse_server(result.stdout)


def load_refresh_record(path: Path = RUN_RECORD_PATH) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60)


def find_refresh_push(since: datetime, git: Callable = _git) -> str | None:
    """Hash of the first "Daily refresh" commit on origin/main made at or after `since`, if any."""
    git("fetch", "-q", "origin")  # best effort: offline still checks the last known origin/main
    out = git("log", "origin/main", "--reverse", f"--since={since.isoformat()}", "--format=%H",
              "--grep=^Daily refresh").stdout.split()
    return out[0] if out else None


def deployed(commit: str, head: str, git: Callable = _git) -> bool:
    return bool(head) and git("merge-base", "--is-ancestor", commit, head).returncode == 0


def review_reasons(review: list[dict]) -> Counter:
    """Count needs-review markets per reason, without the per-game details (dates, team names)."""
    reasons = Counter()
    for r in review:
        for reason in r["reason"].split("; "):
            reasons[re.sub(r"\s*\(.*?\)", "", reason.split(":")[0])] += 1
    return reasons


# --- judging ------------------------------------------------------------------


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60} min"


def server_checks(s: Server, now: datetime) -> list[Check]:
    checks = [Check("Scanner service", s.service == "active", s.service)]
    if s.last_scan_at is None:
        checks.append(Check("Last scan", False, "no finished scan in the log"))
    else:
        age = now - s.last_scan_at
        checks.append(Check("Last scan", age <= MAX_SCAN_AGE,
                            f"{s.last_scan_at:%Y-%m-%d %H:%M} UTC, {_age(age)} ago, {s.last_scan_pairs} pairs"))
    checks.append(Check("Errors (last hour)", s.errors_last_hour == 0,
                        f"{s.errors_last_hour} in {s.scans_last_hour} scans"))
    checks.append(Check("Disk", s.disk_used_pct < DISK_FULL_PCT,
                        f"{s.disk_used_pct}% used, {s.disk_free_kb / 1024 / 1024:.1f} GB free"))
    return checks


def refresh_checks(record: dict | None, now: datetime, server_head: str,
                   find_push: Callable = find_refresh_push, is_deployed: Callable = deployed) -> list[Check]:
    if record is None:
        return [Check("Daily refresh", False, "no record yet (data/refresh_last.json); has it run on this machine?")]
    run_at = datetime.fromisoformat(record["run_at"])
    age = now - run_at
    by_league = ", ".join(f"{k} {v}" for k, v in sorted(record["ready_by_league"].items()))
    found = f"{record['ready']} ready" + (f" ({by_league})" if by_league else "")
    checks = [Check("Daily refresh", age <= MAX_REFRESH_AGE,
                    f"{run_at:%Y-%m-%d %H:%M} UTC, {_age(age)} ago, {found}")]
    if record["ready"] == 0:
        checks.append(Check("Pushed?", True, "nothing to approve, so nothing to push"))
    else:
        commit = find_push(run_at)
        if commit is None:
            applied = record.get("applied")
            why = "approved locally but no push found" if applied else "not applied"
            checks.append(Check("Pushed?", False, f"no 'Daily refresh' commit on origin/main ({why})"))
        else:
            where = "deployed on the droplet" if is_deployed(commit, server_head) else "droplet hasn't pulled it yet"
            checks.append(Check("Pushed?", True, f"{commit[:7]}, {where}"))
    return checks


def render(checks: list[Check], review: list[dict]) -> str:
    lines = [f"{'OK  ' if c.ok else 'FAIL'}  {c.name:20} {c.detail}" for c in checks]
    games = {r["kalshi_ticker"].rsplit("-", 1)[0] for r in review}
    lines.append(f"INFO  {'Needs review':20} {len(review)} markets in {len(games)} games")
    for reason, n in review_reasons(review).most_common():
        lines.append(f"{'':27}{n:4}  {reason}")
    healthy = all(c.ok for c in checks)
    lines.append("\nHEALTHY" if healthy else "\nNEEDS ATTENTION: " + ", ".join(c.name for c in checks if not c.ok))
    return "\n".join(lines)


def main() -> int:
    now = datetime.now(timezone.utc)
    host = droplet_host()
    checks: list[Check] = []
    head = ""
    if not host:
        checks.append(Check("Droplet", False, "DROPLET_HOST is not set (add it to .env, e.g. DROPLET_HOST=root@<ip>)"))
    else:
        try:
            server = fetch_server(host)
            head = server.head
            checks += server_checks(server, now)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
            checks.append(Check("Droplet", False, f"can't read status over SSH: {e}"))
    record = load_refresh_record()
    checks += refresh_checks(record, now, head)
    print(render(checks, record["review"] if record else []))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
