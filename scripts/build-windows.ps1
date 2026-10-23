$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Build this Windows application on Windows.'
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    & uv sync --frozen --extra desktop --group build
    if ($LASTEXITCODE -ne 0) { throw 'Dependency synchronization failed.' }
    & uv run --no-sync python -c "import tkinter; tkinter.Tcl()"
    if ($LASTEXITCODE -ne 0) { throw 'Python with working Tk/Tcl is required to build the desktop app.' }
    & uv run --no-sync python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped.' }
    & uv run --no-sync pyinstaller --noconfirm --clean PrintBridge.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $Bundle = Join-Path $ProjectRoot 'dist\PrintBridge'
    $RequiredFiles = @(
        'PrintBridge.exe',
        '_internal\static\index.html',
        '_internal\templates\admin.html',
        '_internal\drivers\PrintBridge.ppd'
    )
    foreach ($RelativePath in $RequiredFiles) {
        if (-not (Test-Path (Join-Path $Bundle $RelativePath) -PathType Leaf)) {
            throw "Bundle is missing: $RelativePath"
        }
    }
    Copy-Item 'docs\portable-windows.md' (Join-Path $Bundle 'DEPLOYMENT.md')
    $Archive = Join-Path $ProjectRoot 'dist\PrintBridge-windows.zip'
    Compress-Archive -Path $Bundle -DestinationPath $Archive -Force
    Write-Host "Created: $Archive"
    Write-Host 'Extract the entire folder. Windows desktop and real printing still require acceptance testing.'
}
finally {
    Pop-Location
}
