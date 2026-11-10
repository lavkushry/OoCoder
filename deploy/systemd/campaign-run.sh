#!/usr/bin/env bash
# Started by oocoder-campaign.service and oocoder-campaign-watch.service.
# Kept out of the unit file so systemd does not expand $OPEN_LIMIT or $EXTRA.
set -euo pipefail
cd /opt/oocoder
args=(
  campaign watch
  --interval "${WATCH_INTERVAL_SEC:-300}"
  --max-ticks "${WATCH_MAX_TICKS:-1}"
  --runtime "${OOCODER_RUNTIME:-local}"
  --model "${OOCODER_MODEL:-composer-2.5}"
)
if [[ "${OPEN_PRS:-0}" == "1" ]]; then
  args+=(--open-prs)
fi
if [[ -n "${OPEN_LIMIT:-}" ]]; then
  args+=(-n "${OPEN_LIMIT}")
fi
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  args+=(--dry-run)
fi
exec node packages/cli/dist/bin.js "${args[@]}"
