$ErrorActionPreference = "SilentlyContinue"

$repoDir = "C:\Users\carlo\OneDrive\Desktop\Warren Buffett Jr\repo"
$python  = Join-Path $repoDir "engine\.venv\Scripts\python.exe"
$webapp  = Join-Path $repoDir "engine\scripts\webapp.py"
$chrome  = "C:\Users\carlo\AppData\Local\Google\Chrome\Application\chrome.exe"
$port    = 8765
$url     = "http://localhost:$port"
# Generous timeout: right after boot, OneDrive may still be rehydrating
# .venv files and antivirus may be scanning python.exe for the first time,
# both of which can push webapp.py's startup well past a couple seconds.
$timeoutSeconds = 90
$errLog  = Join-Path $repoDir "engine\webapp_error.log"

$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath $python -ArgumentList "`"$webapp`"" -WorkingDirectory $repoDir -WindowStyle Hidden -RedirectStandardError $errLog

    $elapsed = 0
    $ready = $false
    while ($elapsed -lt $timeoutSeconds) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
        $elapsed++
    }

    if (-not $ready) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "Warren Buffett Jr no arranco a tiempo (espere $timeoutSeconds segundos). Puede que OneDrive siga sincronizando archivos tras encender la PC, o que el servidor haya fallado. Revisa el log: $errLog",
            "Warren Buffett Jr"
        ) | Out-Null
    }
}

if (Test-Path $chrome) {
    Start-Process -FilePath $chrome -ArgumentList $url
} else {
    Start-Process $url
}
