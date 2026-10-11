#!/bin/bash
# Run the alt-arb scanner as a macOS background service (launchd LaunchAgent).
#
#   scripts/scanner-service.sh install     start now and at every login; restarts if it crashes
#   scripts/scanner-service.sh status      is it running, and the last few scans
#   scripts/scanner-service.sh logs        follow the log (Ctrl+C to stop following)
#   scripts/scanner-service.sh uninstall   stop it and remove the service
#
# caffeinate -is keeps the Mac from idle-sleeping (and from system sleep on power) while
# the scanner runs. Closing the lid still sleeps the Mac; scans resume when it wakes.
set -euo pipefail

LABEL="com.idownham.altarb.scanner"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$REPO/data/scanner.log"
DOMAIN="gui/$(id -u)"

install() {
  if [ ! -x "$REPO/.venv/bin/python" ]; then
    echo "No virtualenv at $REPO/.venv - see README.md Setup first." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$PLIST")" "$REPO/data"
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true  # replace any running copy
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-is</string>
    <string>$REPO/.venv/bin/python</string>
    <string>-m</string>
    <string>altarb.scanner</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
</dict>
</plist>
EOF
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "Installed and started. Logs: $LOG"
  echo "Check on it with: scripts/scanner-service.sh status"
}

uninstall() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "Stopped." || echo "Was not running."
  rm -f "$PLIST" && echo "Removed $PLIST"
}

status() {
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    # Top-level properties only (one tab of indent); nested sections repeat "state".
    launchctl print "$DOMAIN/$LABEL" | grep -E $'^\t(state|pid|runs|last exit code) =' | sed $'s/^\t/  /'
  else
    echo "  not installed"
  fi
  if [ -f "$REPO/data/scanner.db" ]; then
    echo "Last scans (UTC):"
    sqlite3 -column -header "$REPO/data/scanner.db" \
      "SELECT id, substr(started_at, 1, 19) AS started, pairs_checked AS pairs, errors,
              CAST(round((julianday(finished_at) - julianday(started_at)) * 86400) AS INT) AS secs
       FROM scans ORDER BY id DESC LIMIT 5;"
    echo "Opportunities logged: $(sqlite3 "$REPO/data/scanner.db" 'SELECT COUNT(*) FROM opportunities;')"
  fi
}

logs() {
  tail -n 20 -f "$LOG"
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  logs) logs ;;
  *) echo "usage: $0 {install|uninstall|status|logs}" >&2; exit 2 ;;
esac
