#!/bin/bash
# One-time setup of the alt-arb scanner on a fresh Ubuntu 24.04 server. Run as root:
#
#   curl -fsSL <this file> -o setup.sh   (or copy it over)   then:   bash setup.sh
#
# Safe to re-run. The first run creates a read-only GitHub deploy key, prints it, and stops;
# add it to the repo (Settings -> Deploy keys, leave "Allow write access" OFF), then re-run.
#
# REPO_URL overrides the clone source; SKIP_SYSTEMD=1 skips service install (used for testing).
set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:idownham/alt-arb.git}"
APP=/opt/alt-arb
USER_HOME=/home/altarb
# GitHub's published SSH host key (docs.github.com "GitHub's SSH key fingerprints"), pinned so
# the first connection can't be intercepted. If GitHub rotates it, update this line.
GITHUB_HOST_KEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"

as_app() { runuser -u altarb -- "$@"; }
step() { printf '\n==> %s\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "run as root (sudo bash setup.sh)" >&2; exit 1; }

step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv git sqlite3 openssh-client >/dev/null

step "Creating the 'altarb' service user"
id altarb >/dev/null 2>&1 || useradd --system --create-home --home-dir "$USER_HOME" --shell /usr/sbin/nologin altarb
mkdir -p "$APP" && chown altarb:altarb "$APP"

if [[ "$REPO_URL" == git@github.com:* ]]; then
  step "GitHub deploy key"
  install -d -m 700 -o altarb -g altarb "$USER_HOME/.ssh"
  grep -qxF "$GITHUB_HOST_KEY" "$USER_HOME/.ssh/known_hosts" 2>/dev/null || echo "$GITHUB_HOST_KEY" >> "$USER_HOME/.ssh/known_hosts"
  chown altarb:altarb "$USER_HOME/.ssh/known_hosts"
  [ -f "$USER_HOME/.ssh/id_ed25519" ] || as_app ssh-keygen -q -t ed25519 -N "" -C "alt-arb server (read-only deploy key)" -f "$USER_HOME/.ssh/id_ed25519"
  if ! as_app git ls-remote "$REPO_URL" >/dev/null 2>&1; then
    echo
    echo "GitHub doesn't accept this server's key yet. Add it as a READ-ONLY deploy key:"
    echo "  https://github.com/idownham/alt-arb/settings/keys/new"
    echo "  Title: alt-arb server     Allow write access: OFF     Key:"
    echo
    cat "$USER_HOME/.ssh/id_ed25519.pub"
    echo
    echo "Then run this script again."
    exit 2
  fi
fi

step "Fetching the code"
if [ -d "$APP/.git" ]; then
  as_app git -C "$APP" pull --ff-only --quiet
else
  as_app git clone --quiet "$REPO_URL" "$APP"
fi

step "Building the Python environment"
[ -x "$APP/.venv/bin/python" ] || as_app python3 -m venv "$APP/.venv"
as_app "$APP/.venv/bin/python" -m pip install --quiet --upgrade pip
as_app "$APP/.venv/bin/python" -m pip install --quiet -e "$APP[dev]"
chmod +x "$APP/deploy/update.sh"

step "Running the offline tests"
(cd "$APP" && as_app "$APP/.venv/bin/python" -m pytest -q -p no:cacheprovider)

if [ "${SKIP_SYSTEMD:-0}" = 1 ]; then
  step "SKIP_SYSTEMD=1: not installing services"
  exit 0
fi

step "Installing and starting services"
cp "$APP"/deploy/*.service "$APP"/deploy/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now altarb-scanner.service altarb-update.timer
sleep 3
systemctl --no-pager --lines=5 status altarb-scanner.service || true

cat <<EOF

Done. The scanner is running and will survive reboots.
  Logs:          journalctl -u altarb-scanner -f
  Last scans:    sqlite3 $APP/data/scanner.db "SELECT id, started_at, pairs_checked, errors FROM scans ORDER BY id DESC LIMIT 5;"
  Auto-updates:  every 15 min (journalctl -u altarb-update)
EOF
