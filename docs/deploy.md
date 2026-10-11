# Running the scanner on a cloud server

The laptop service pauses whenever the lid is closed. A small cloud server scans around the clock.
Cost: about $5–6/month. Everything after step 2 is one script.

## What runs on the server

| Piece | What it does |
| - | - |
| `altarb-scanner` (systemd) | Runs `python -m altarb.scanner`; starts at boot, restarts 60s after any crash |
| `altarb-update` (systemd timer, every 15 min) | `git pull`. New `pairs.csv` rows are picked up on the next scan. If *code* changed, it runs the tests and restarts the scanner only if they pass; otherwise it rolls the checkout back |
| Read-only deploy key | The server can read this one repo and nothing else; it can't push |

Data stays on the server in `/opt/alt-arb/data/scanner.db`.

## Steps

1. **Create a server** (you do this: it needs an account and payment). Any provider works; e.g.
   Hetzner Cloud (CPX11, Ashburn, ~$5/mo) or DigitalOcean (Basic $6/mo droplet, NYC). Choose:
   - Image: **Ubuntu 24.04**
   - Region: **United States** (both venues are US exchanges)
   - Authentication: **SSH key** — add your Mac's public key (`cat ~/.ssh/id_ed25519.pub`; create one
     with `ssh-keygen -t ed25519` if you don't have one)
2. **Connect:** `ssh root@<server-ip>`
3. **Run the setup script.** The repo is private, so copy the script over from your Mac:
   ```bash
   scp deploy/setup.sh root@<server-ip>:     # from the alt-arb folder on your Mac
   ssh root@<server-ip> bash setup.sh
   ```
4. **Add the deploy key.** The first run prints a public key and stops. Add it at
   https://github.com/idownham/alt-arb/settings/keys/new with **Allow write access OFF**, then run
   `ssh root@<server-ip> bash setup.sh` again. It installs everything, runs the tests, and starts the scanner.
5. **Stop the laptop scanner** so you're not collecting two half-overlapping logs:
   `scripts/scanner-service.sh uninstall` (your existing `data/scanner.db` stays on the Mac).

## Day to day

```bash
ssh root@<server-ip> journalctl -u altarb-scanner -f          # live log
ssh root@<server-ip> journalctl -u altarb-update --since today # what auto-update did
ssh root@<server-ip> sqlite3 /opt/alt-arb/data/scanner.db \
  "SELECT id, started_at, pairs_checked, errors FROM scans ORDER BY id DESC LIMIT 5;"
scp root@<server-ip>:/opt/alt-arb/data/scanner.db data/server-scanner.db   # copy the log home
```

Approving new pairs works as before: commit and push `pairs.csv`, and the server has them within
15 minutes.
