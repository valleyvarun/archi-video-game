# Start the viewer locally on Windows. Extra arguments go to viewer.py, e.g.
#   .\server\run.ps1 --stage C:\path\to\scene.usd
Set-Location $PSScriptRoot
$env:OVRTX_SKIP_USD_CHECK = "1"
& .\.venv\Scripts\python.exe viewer.py @args
