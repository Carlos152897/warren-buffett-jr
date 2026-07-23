$ErrorActionPreference = "SilentlyContinue"

$repoDir = "C:\Users\carlo\OneDrive\Desktop\Warren Buffett Jr\repo"
$python  = Join-Path $repoDir "engine\.venv\Scripts\python.exe"
$webapp  = Join-Path $repoDir "engine\scripts\webapp.py"
$chrome  = "C:\Users\carlo\AppData\Local\Google\Chrome\Application\chrome.exe"
$port    = 8765
$url     = "http://localhost:$port"

$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath $python -ArgumentList "`"$webapp`"" -WorkingDirectory $repoDir -WindowStyle Hidden
    Start-Sleep -Seconds 2
}

if (Test-Path $chrome) {
    Start-Process -FilePath $chrome -ArgumentList $url
} else {
    Start-Process $url
}
