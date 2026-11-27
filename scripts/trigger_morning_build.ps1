# Checks if today's edition of The David Times is already live on GitHub Pages.
# If not, triggers the GitHub Actions build via authenticated `gh` CLI.
$ErrorActionPreference = "SilentlyContinue"
$todayIso = (Get-Date).ToString("yyyy-MM-dd")
$latestUrl = "https://lacozy.github.io/davids-times/latest.json"

try {
    $meta = Invoke-RestMethod -Uri $latestUrl -TimeoutSec 10
    if ($meta.date_iso -eq $todayIso) {
        Write-Output "Already published for $todayIso — nothing to do."
        exit 0
    }
} catch {
    # Proceed to trigger if latest.json check fails
}

$ghExe = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $ghExe) {
    $ghExe = "C:\Program Files\GitHub CLI\gh.exe"
}
if (Test-Path $ghExe) {
    & $ghExe workflow run "Daily Kindle Newspaper" --repo lacozy/davids-times
    Write-Output "Triggered Daily Kindle Newspaper for $todayIso (exit=$LASTEXITCODE)."
}
