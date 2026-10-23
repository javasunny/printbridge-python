$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
# Copy this file to start-windows.local.ps1 and edit that ignored local copy.
$env:PRINT_MODE = 'simulate'
$env:PRINT_HOST = '127.0.0.1'
$env:PRINT_PORT = '8765'
$env:PRINT_IPP_ENABLED = '0' # Set to 1 to enable /ipp/print
$env:PRINT_PRINTER = 'TOSHIBA e-STUDIO2823AMSeries GDI'
$env:PRINT_SUMATRA = 'C:\Program Files\SumatraPDF\SumatraPDF.exe'
& "$PSScriptRoot\.venv\Scripts\python.exe" -m printbridge
exit $LASTEXITCODE
