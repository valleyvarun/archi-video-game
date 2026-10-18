# One-time setup on a Windows PC with an NVIDIA RTX GPU (for local testing).
# Safe to run again: it only adds what is missing.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$BlenderVersion = "4.5.14"

# Renderer packages (ovrtx, ovstage, ovstream)
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe fetch_client.py

# Converters: NVIDIA CAD Converter (Python 3.12) and Blender
if (-not (Test-Path .venv-convert)) { py -3.12 -m venv .venv-convert }
.\.venv-convert\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv-convert\Scripts\python.exe -m pip install -r requirements-convert.txt
$blender = "tools\blender-$BlenderVersion-windows-x64\blender.exe"
if (-not (Test-Path $blender)) {
    New-Item -ItemType Directory -Force tools | Out-Null
    curl.exe -L -o tools\blender.zip "https://download.blender.org/release/Blender4.5/blender-$BlenderVersion-windows-x64.zip"
    Expand-Archive tools\blender.zip -DestinationPath tools
    Remove-Item tools\blender.zip
}

.\.venv\Scripts\python.exe -c "import ovrtx, ovstage, ovstream; print('ovrtx', ovrtx.__version__, '| ovstage', ovstage.__version__, '| ovstream', ovstream.get_version())"
.\.venv-convert\Scripts\python.exe -c "import usd_convert_cad; from pxr import Usd; print('CAD converter', usd_convert_cad.__version__, '| USD', Usd.GetVersion())"
Write-Host "Setup complete. Start the server with: .\server\run.ps1"
