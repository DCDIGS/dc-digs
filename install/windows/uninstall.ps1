# Stop DC Digs and remove its start-up entry and Start menu shortcut. The folder with
# your inbox and settings stays; delete it yourself if you want everything gone.
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*dig.web*" -and $_.ExecutablePath -like "$Root*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Stopped DC Digs." }
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath("Startup")) "DC Digs.vbs")
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath("Programs")) "DC Digs.url")
Write-Host "DC Digs won't start at sign-in any more."
Write-Host "Your inbox and settings are still in $Root (data\ and criteria.yaml)."
