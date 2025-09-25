param(
    [switch]$Clean,
    [switch]$OneFile,
    [switch]$Debug
)

if ($Clean) {
    Write-Host "Cleaning dist and build folders" -ForegroundColor Cyan
    Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
}

$ErrorActionPreference = 'Stop'

# Ensure PyInstaller is available (quietly)
Write-Host "Checking PyInstaller..." -ForegroundColor DarkGray
$pyi = & python -m pip show pyinstaller 2>$null | Out-String
if (-not $pyi) {
    Write-Host "PyInstaller not found. Installing..." -ForegroundColor Yellow
    python -m pip install --upgrade pip | Out-Null
    python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller (exit code $LASTEXITCODE)" }
} else {
    Write-Host "PyInstaller present." -ForegroundColor DarkGray
}

Write-Host "Config: OneFile=$OneFile Debug=$Debug" -ForegroundColor DarkGray

$commonArgs = @("--noconfirm", "--clean")
if ($Debug) { $commonArgs += @("--log-level", "DEBUG") }

# GUI build only
$guiArgs = $commonArgs + @("--name", "ControllerGUI", "entry_gui.py")
if (-not $Debug) { $guiArgs += "--windowed" } else { $guiArgs += "--console" }
if ($OneFile) { $guiArgs += "--onefile" }

Write-Host "Building GUI executable" -ForegroundColor Cyan
python -m PyInstaller @guiArgs

Write-Host "Done. GUI executable in dist/ControllerGUI" -ForegroundColor Green
