# Operator runbook

## Install (Linux)

1. Python 3.12+, rootless Podman on the Linux host, git. Podman runs with `--userns=keep-id` and `--network=none` when the allowlist is empty. The sandbox work directory is `/var/lib/oss-agent/sandbox` (see the orchestrator unit). A macOS machine uses Fake isolation and does not start that Podman command.
2. `make setup`
3. Create user: `useradd -r -m -d /var/lib/oss-agent oss-agent`
4. Copy systemd units from `deploy/systemd/` to `/etc/systemd/system/`.
5. Put GitHub App credentials in `/etc/oss-agent/github_app.env` (root-owned, mode `0400`).
6. `systemctl enable --now oss-agent-orchestrator.service`

## Config

Environment prefix `OSS_AGENT_`. Nested fields use `__`, e.g.:

```bash
export OSS_AGENT_POLICY__KILL_SWITCH=true
export OSS_AGENT_POLICY__AUTO_APPROVE__COMMENT=false
export OSS_AGENT_DATABASE_URL=sqlite:////var/lib/oss-agent/oss_agent.db
```

Default: every outward action requires human approval.

## Kill switch

```bash
export OSS_AGENT_POLICY__KILL_SWITCH=true
# or
oss-agent kill-switch --enable
```

Gateway checks the flag before every send.

## Approval queue

```bash
oss-agent queue
oss-agent approve act_xxx --by sponsor
```

UI binds to `127.0.0.1` only; use SSH tunnel if remote.

## Backup

```bash
sqlite3 /var/lib/oss-agent/oss_agent.db ".backup '/var/backups/oss_agent.db'"
cp -a /var/lib/oss-agent/audit /var/backups/oss_agent_audit
```

Restore by stopping services, replacing the DB file, and starting again.

## OoCoder DE campaign 24×7 (Linux)

For continuous allowlist monitoring (sync / scrub / monitor / fixci):

See [CAMPAIGN_LINUX_24X7.md](CAMPAIGN_LINUX_24X7.md).

```bash
sudo systemctl enable --now oocoder-campaign.timer
journalctl -u oocoder-campaign.service -f
```

## Add a project

See [ADD_PROJECT.md](ADD_PROJECT.md).
