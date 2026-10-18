#!/usr/bin/env bash
# One-time setup on a Linux NVIDIA GPU machine (for example a Brev instance).
set -euo pipefail
cd "$(dirname "$0")"

echo "== System packages"
sudo apt-get update -qq
PACKAGES="python3-venv python3-pip libgomp1 libatomic1 libgl1 libglx0 libegl1 libx11-6 libxau6 libxdmcp6 libxcb1 xvfb tmux curl"
sudo apt-get install -y -qq $PACKAGES libglib2.0-0 || sudo apt-get install -y -qq $PACKAGES libglib2.0-0t64

echo "== Python packages (ovrtx, ovstage, ovstream: a few GB)"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt

echo "== NVIDIA browser client"
.venv/bin/python fetch_client.py

echo "== Caddy (HTTPS front door)"
if ! command -v caddy >/dev/null; then
  curl -sL -o /tmp/caddy.tar.gz https://github.com/caddyserver/caddy/releases/download/v2.8.4/caddy_2.8.4_linux_amd64.tar.gz
  tar -xzf /tmp/caddy.tar.gz -C /tmp caddy
  sudo mv /tmp/caddy /usr/local/bin/caddy
fi
# Let Caddy listen on ports 80/443 without running as root.
sudo setcap cap_net_bind_service=+ep "$(command -v caddy)"

echo "== cloudflared (free public https:// address)"
if ! command -v cloudflared >/dev/null; then
  curl -sL -o /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared
fi

echo "== Check"
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
.venv/bin/python -c "import ovrtx, ovstage, ovstream; print('ovrtx', ovrtx.__version__, '| ovstage', ovstage.__version__, '| ovstream', ovstream.get_version())"
echo "Setup complete. Start the server with: bash server/run.sh"
