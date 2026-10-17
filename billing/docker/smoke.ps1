#Requires -Version 7
$ErrorActionPreference = 'Stop'
$base = 'http://localhost:8080'

function Assert-HttpOk([string]$Url, [string]$Contains = $null) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -MaximumRedirection 0
    } catch {
        if ($_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
            throw "Expected 200 from $Url got $code"
        }
        throw
    }
    if ($r.StatusCode -ne 200) { throw "Expected 200 from $Url got $($r.StatusCode)" }
    if ($Contains -and ($r.Content -notmatch [regex]::Escape($Contains))) {
        throw "Expected body to contain '$Contains' from $Url"
    }
    return $r
}

Write-Host 'Oracle: GET /up'
Assert-HttpOk "$base/up" 'ok'

Write-Host 'Oracle: session cookie via /login'
$login = Assert-HttpOk "$base/login"
if (-not $login.Headers['Set-Cookie']) { throw 'No Set-Cookie from /login (storage/sessions?)' }

Write-Host 'Oracle: Vite manifest'
Assert-HttpOk "$base/build/manifest.json"

Write-Host 'Oracle: artisan'
docker compose exec -T app php artisan --version

Write-Host 'Oracle: Snappy PDF generation (not just -x)'
$pdfLen = (docker compose exec -T app php docker/smoke-pdf.php 2>&1 | Out-String).Trim()
if (-not $pdfLen -or [int]$pdfLen -le 0) { throw "Snappy PDF oracle failed (len=$pdfLen)" }

Write-Host 'SMOKE OK'
