#!/usr/bin/env bash
# One-time setup on a Linux NVIDIA GPU machine (for example a Brev instance).
# Safe to run again (for example after `git pull`): it only adds what is missing.
set -euo pipefail
cd "$(dirname "$0")"
BLENDER_VERSION=4.5.14

echo "== System packages"
sudo apt-get update -qq
PACKAGES="python3-venv python3-pip libgomp1 libatomic1 libgl1 libglx0 libegl1 libx11-6 libxau6 libxdmcp6 libxcb1
  libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 xz-utils xvfb tmux curl"
sudo apt-get install -y -qq $PACKAGES libglib2.0-0 || sudo apt-get install -y -qq $PACKAGES libglib2.0-0t64

echo "== Renderer packages (ovrtx, ovstage, ovstream: a few GB)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt

echo "== NVIDIA browser client"
.venv/bin/python fetch_client.py

echo "== Converters: NVIDIA CAD Converter (needs Python 3.12, installed with uv) and Blender"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -d .venv-convert ] || uv venv --python 3.12 .venv-convert
uv pip install --python .venv-convert/bin/python -r requirements-convert.txt
if [ ! -x "tools/blender-$BLENDER_VERSION-linux-x64/blender" ]; then
  mkdir -p tools
  curl -sL "https://download.blender.org/release/Blender4.5/blender-$BLENDER_VERSION-linux-x64.tar.xz" | tar -xJ -C tools
fi

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
.venv-convert/bin/python -c "import usd_convert_cad; from pxr import Usd; print('CAD converter', usd_convert_cad.__version__, '| USD', Usd.GetVersion())"
"tools/blender-$BLENDER_VERSION-linux-x64/blender" --version | head -1
echo "Setup complete. Start the server with: bash server/run.sh"
