# Install DC Digs on Windows: a Python environment for the app, and a start-up entry
# that runs it in the background (no window) at http://localhost:8765.
# Run it again any time to repair or update; your inbox and settings are kept.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Port = 8765
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern '^DIG_PORT=(\d+)' | Select-Object -First 1
    if ($line) { $Port = [int]$line.Matches[0].Groups[1].Value }
}

function Say($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }

Say "1/4  Looking for Python 3.10 or newer..."
$python = $null
foreach ($candidate in @("py -3", "python", "python3")) {
    $parts = $candidate -split " "
    $exe = $parts[0]
    $rest = @($parts | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = @($exe) + $rest; break }
    } catch { }
}
if (-not $python) {
    Say "Python 3.10 or newer is needed."
    Write-Host "Download it from https://www.python.org/downloads/windows/ ."
    Write-Host "In the installer, tick 'Add python.exe to PATH'. Then run this installer again."
    Start-Process "https://www.python.org/downloads/windows/"
    exit 1
}
$pyExe = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)
Write-Host ("Using " + (& $pyExe @pyArgs --version))

Say "2/4  Setting up the app (this takes a minute or two the first time)..."
& $pyExe @pyArgs -m venv (Join-Path $Root ".venv")
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
$venvPyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { Say "Installing the Python packages failed (see above)."; exit 1 }
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

Say "3/4  Starting DC Digs in the background (and at every sign-in)..."
# Stop an older copy first, so the new one can take the port.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*dig.web*" -and $_.ExecutablePath -like "$Root*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$startup = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startup "DC Digs.vbs"
$vbs = @"
' Starts DC Digs in the background at sign-in (created by DC Digs' installer).
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$Root"
sh.Run """$venvPyw"" -m dig.web", 0, False
"@
Set-Content -Path $launcher -Value $vbs -Encoding Unicode
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$launcher`""
# A Start menu entry that opens the app in the browser.
$programs = [Environment]::GetFolderPath("Programs")
Set-Content -Path (Join-Path $programs "DC Digs.url") -Encoding ASCII -Value "[InternetShortcut]`r`nURL=http://localhost:$Port/`r`n"

Say "4/4  Waiting for it to come up..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/" | Out-Null
        Write-Host "DC Digs is running at http://localhost:$Port"
        Start-Process "http://localhost:$Port/"
        exit 0
    } catch { Start-Sleep -Seconds 1 }
}
Say "DC Digs didn't answer yet."
Write-Host "Have a look at $Root\logs\web.log, or open http://localhost:$Port in a minute."
exit 1
