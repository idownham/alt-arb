#!/bin/bash
# Pull the latest commit; restart the scanner only if code changed AND the tests pass.
# Run as root every 15 minutes by altarb-update.timer.
#
#   pairs.csv / docs only  -> nothing to do: the scanner reloads pairs.csv every scan
#   code changed, tests ok -> reinstall, restart the scanner
#   code changed, tests fail -> roll the checkout back so disk matches the running code
set -euo pipefail

APP=/opt/alt-arb
as_app() { runuser -u altarb -- "$@"; }

before=$(as_app git -C "$APP" rev-parse HEAD)
as_app git -C "$APP" pull --ff-only --quiet
after=$(as_app git -C "$APP" rev-parse HEAD)
[ "$before" = "$after" ] && exit 0
echo "pulled ${before:0:7} -> ${after:0:7}"

if as_app git -C "$APP" diff --quiet "$before" "$after" -- src pyproject.toml deploy; then
  echo "data/docs-only change; the scanner picks up pairs.csv on its next scan"
  exit 0
fi

as_app "$APP/.venv/bin/python" -m pip install --quiet -e "$APP[dev]"
if ! (cd "$APP" && as_app "$APP/.venv/bin/python" -m pytest -q -p no:cacheprovider); then
  echo "tests FAILED at ${after:0:7}; rolling back to ${before:0:7}, scanner keeps running" >&2
  as_app git -C "$APP" reset --hard --quiet "$before"
  as_app "$APP/.venv/bin/python" -m pip install --quiet -e "$APP[dev]"
  exit 1
fi

if ! as_app git -C "$APP" diff --quiet "$before" "$after" -- deploy/*.service deploy/*.timer; then
  cp "$APP"/deploy/*.service "$APP"/deploy/*.timer /etc/systemd/system/
  systemctl daemon-reload
  echo "reinstalled systemd units"
fi
systemctl restart altarb-scanner
echo "tests passed; scanner restarted on ${after:0:7}"
