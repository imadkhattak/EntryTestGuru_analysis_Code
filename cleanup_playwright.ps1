# Cleanup script for Playwright / Chromium leftovers on the C: drive
# Shows the size of each item and asks before deleting it.
# Run in PowerShell:  .\cleanup_playwright.ps1

function Get-SizeMB($path) {
    $items = Get-ChildItem -Path $path -Recurse -Force -ErrorAction SilentlyContinue |
             Where-Object { -not $_.PSIsContainer }
    $sum = ($items | Measure-Object -Property Length -Sum).Sum
    if (-not $sum) { return 0 }
    return [math]::Round($sum / 1MB, 1)
}

$targets = @(
    @{ Name = "Playwright downloaded browsers (re-install with: playwright install chromium)"
       Paths = @("$env:LOCALAPPDATA\ms-playwright") },
    @{ Name = "Playwright / Chromium temp profiles and scoped folders"
       Paths = @("$env:TEMP\playwright*", "$env:TEMP\scoped_dir*", "$env:TEMP\.org.chromium.*") },
    @{ Name = "pip download cache"
       Paths = @("$env:LOCALAPPDATA\pip\Cache") },
    @{ Name = "Windows temp crash dumps from Chromium"
       Paths = @("$env:LOCALAPPDATA\CrashDumps\chrome*", "$env:LOCALAPPDATA\CrashDumps\headless_shell*") }
)

Write-Host "`nClose any Chrome / Chromium windows started by the script first.`n" -ForegroundColor Yellow

$totalFreed = 0
foreach ($t in $targets) {
    $found = @()
    foreach ($p in $t.Paths) {
        $found += Get-Item -Path $p -Force -ErrorAction SilentlyContinue
    }
    if (-not $found) {
        Write-Host "[none]  $($t.Name)" -ForegroundColor DarkGray
        continue
    }

    $size = 0
    foreach ($f in $found) { $size += Get-SizeMB $f.FullName }
    Write-Host "`n$($t.Name)" -ForegroundColor Cyan
    $found | ForEach-Object { Write-Host "   $($_.FullName)" }
    Write-Host "   Size: $size MB"

    $answer = Read-Host "Delete this? (y/n)"
    if ($answer -eq "y") {
        foreach ($f in $found) {
            Remove-Item -Path $f.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
        $totalFreed += $size
        Write-Host "   Deleted." -ForegroundColor Green
    } else {
        Write-Host "   Skipped."
    }
}

Write-Host "`nDone. Freed about $totalFreed MB." -ForegroundColor Green
Write-Host "Note: your project's 'browser_profile' folder (saved login) is on the E: drive. Delete it manually if you want to log out."