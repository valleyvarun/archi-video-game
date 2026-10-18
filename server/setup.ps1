# One-time setup on a Windows PC with an NVIDIA RTX GPU (for local testing).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe fetch_client.py
.\.venv\Scripts\python.exe -c "import ovrtx, ovstage, ovstream; print('ovrtx', ovrtx.__version__, '| ovstage', ovstage.__version__, '| ovstream', ovstream.get_version())"
Write-Host "Setup complete. Start the server with: .\server\run.ps1"
