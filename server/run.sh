#!/usr/bin/env bash
# Start the viewer and its HTTPS front door on a Linux GPU machine.
#   bash server/run.sh                      # grid-only scene, 4 viewer slots
#   bash server/run.sh --stage <usd-url>    # extra arguments go to viewer.py
# Environment:
#   SLOTS      simultaneous viewers (default 4)
#   PUBLIC_IP  this machine's public IP (auto-detected)
#   TUNNEL     "cloudflare" (default) prints a public https:// address; "none" skips it
#              (use "none" if you expose port 80 another way, e.g. a public Brev link)
set -euo pipefail
cd "$(dirname "$0")"

SLOTS="${SLOTS:-4}"
TUNNEL="${TUNNEL:-cloudflare}"
PUBLIC_IP="${PUBLIC_IP:-$(curl -sf https://api.ipify.org || curl -sf https://ifconfig.me)}"
export OVRTX_SKIP_USD_CHECK=1
mkdir -p logs

# ovrtx needs an X display for GPU rendering; use a virtual one on headless machines.
if [ -z "${DISPLAY:-}" ] && command -v Xvfb >/dev/null; then
  Xvfb :99 -screen 0 1280x720x24 >/dev/null 2>&1 &
  export DISPLAY=:99
fi

# Caddy routes the public https:// traffic to the viewer (see deploy/Caddyfile).
caddy stop >/dev/null 2>&1 || true
caddy start --config ../deploy/Caddyfile --adapter caddyfile >/dev/null 2>&1

if [ "$TUNNEL" = "cloudflare" ]; then
  pkill -f "cloudflared tunnel" >/dev/null 2>&1 || true
  cloudflared tunnel --no-autoupdate --url http://localhost:80 > logs/tunnel.log 2>&1 &
  for _ in $(seq 1 30); do
    URL=$(grep -o -E 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1 || true)
    [ -n "$URL" ] && break
    sleep 1
  done
  echo "======================================================================"
  echo " Public server address: ${URL:-<not found, see server/logs/tunnel.log>}"
  echo " Put it in config.js (servers: ['$URL']) or open your site with"
  echo " ?server=$URL"
  echo "======================================================================"
fi

echo "Public IP: $PUBLIC_IP  (UDP ports 47998-$((47998 + SLOTS - 1)) must be open to it)"
.venv/bin/python viewer.py --slots "$SLOTS" --public-ip "$PUBLIC_IP" "$@" 2>&1 | tee logs/viewer.log
