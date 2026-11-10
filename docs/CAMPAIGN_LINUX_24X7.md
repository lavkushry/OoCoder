# Run OoCoder campaign monitoring 24×7 on Linux

Continuous DE-campaign loop on a Linux server: **sync → scrub → monitor → (optional open PRs) → fixci**.

Prefer **systemd**. Cursor Desktop is for interactive work; the server loop is OoCoder itself (optionally using a Cursor API key for implement turns).

## What runs

```bash
oocoder campaign watch --interval 300 --max-ticks 0
# add --open-prs -n 3 when you want new PRs opened automatically
```

Each tick:

1. `campaign sync` — import your open allowlisted PRs
2. `campaign scrub` — strip branding footers
3. `campaign monitor` — CHANGES_REQUESTED / human comments / CI / CLA
4. optional `campaign run` — open PRs toward `campaign.targetPrs`
5. `fixci` — repair CI on PRs OoCoder opened

Learnings: `oocoder campaign learn …` → `docs/CAMPAIGN_RULE_LOG.md`.

## Install (systemd)

Requirements: Node 22+, `gh` authenticated as the contribution user, repo checkout, built CLI.

```bash
sudo useradd -r -m -d /var/lib/oocoder -s /usr/sbin/nologin oocoder || true
sudo mkdir -p /opt/oocoder /etc/oocoder /var/log/oocoder
sudo rsync -a --delete ./ /opt/oocoder/   # or git clone + pull
cd /opt/oocoder
sudo -u oocoder bash -lc 'corepack enable && pnpm install && pnpm --filter @oocoder/core exec tsc -p tsconfig.json && pnpm --filter @oocoder/cli exec tsc -p tsconfig.json'
sudo -u oocoder bash -lc 'cp -n configs/allowlist.yaml configs/policy.yaml /var/lib/oocoder/; gh auth status'
sudo cp deploy/systemd/oocoder-campaign.env.example /etc/oocoder/campaign.env
sudo cp deploy/systemd/oocoder-campaign.service \
        deploy/systemd/oocoder-campaign.timer \
        deploy/systemd/oocoder-campaign-watch.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
```

### Option A — timer (recommended)

Fires one tick every 5 minutes (survives restarts, less memory):

```bash
sudo systemctl enable --now oocoder-campaign.timer
systemctl list-timers oocoder-campaign.timer
journalctl -u oocoder-campaign.service -f
```

### Option B — long-running watch

```bash
sudo systemctl enable --now oocoder-campaign-watch.service
journalctl -u oocoder-campaign-watch.service -f
```

### Open new PRs from the server

`campaign run` / `watch --open-prs` need a model key. Without one, OoCoder fail-fasts (no issue thrash).

In `/etc/oocoder/campaign.env`:

```bash
OPEN_PRS=1
OPEN_LIMIT=3
CURSOR_API_KEY_FILE=/etc/oocoder/cursor_api_key
# or: GROK_API_KEY=xai-... / XAI_API_KEY=xai-...
```

```bash
sudo install -m 0640 -o root -g oocoder /dev/null /etc/oocoder/cursor_api_key
# paste the key into that file (one line), then:
sudo systemctl restart oocoder-campaign.timer
```

Keep `OPEN_PRS=0` for monitor-only (no model key required).

## Cursor Agent vs server loop

| Surface | Role |
| --- | --- |
| **systemd `campaign watch`** | 24×7 monitoring / scrub / fixci / optional PR open on the Linux host |
| **Cursor Desktop / Cloud Agent** | Interactive rework, rule-log entries, one-off deep fixes |
| **Cursor Automations** (optional) | Schedule a Cloud Agent that SSHs or runs the same `campaign watch --max-ticks 1` |

Do **not** expect Cursor Desktop itself to stay open 24×7 on the server. Put OoCoder under systemd; use Cursor when a human (or Cloud Agent) should change code or encode learnings.

## Trino CLA

Trino is **not** cla-assistant.io. After emailing the signed PDF to `cla@trino.io`, processing can take a few days. Until `trinodb/cla` lists you, `cla-signed` stays red even on approved PRs (e.g. #32057).

## Safety

- Rate limits stay in `~/.oocoder/policy.yaml` / `/var/lib/oocoder/policy.yaml`.
- Default outward actions still go through your normal OoCoder gates.
- Kill by stopping the timer/service: `sudo systemctl stop oocoder-campaign.timer`.
