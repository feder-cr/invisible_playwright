<#
Download geckodriver for Windows x64 at a pinned version and check the zip.

New for the Windows reference (the Linux image copied a geckodriver 0.37.1 binary in by hand).

  - the zip sha256 must equal -ZipSha256 (the value pinned in the workflow) when one is given
  - it must also equal the digest GitHub records for that release asset (api.github.com, same token as the rest of the job);
    a mismatch against either fails the step. With no pinned value the API digest alone is checked, a warning says so, and the
    value seen is printed so it can be written into the workflow
  - record the version line and the hashes in <OutDir>\geckodriver_info.json; export GECKODRIVER_BIN
#>
param(
    [Parameter(Mandatory)][string]$Version,
    [string]$ZipSha256 = '',
    [Parameter(Mandatory)][string]$WorkDir,
    [Parameter(Mandatory)][string]$OutDir
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$name = "geckodriver-v$Version-win64.zip"
$url = "https://github.com/mozilla/geckodriver/releases/download/v$Version/$name"
$dir = Join-Path $WorkDir 'geckodriver'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$zip = Join-Path $dir $name
for ($i = 1; $i -le 4; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip -TimeoutSec 300; break }
    catch { if ($i -eq 4) { throw }; Write-Host "download failed ($i): $($_.Exception.Message)"; Start-Sleep -Seconds (5 * $i) }
}
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
Write-Host "zip sha256        $actual"

$headers = @{ 'Accept' = 'application/vnd.github+json' }
if ($env:GITHUB_TOKEN) { $headers['Authorization'] = "Bearer $env:GITHUB_TOKEN" }
$rel = Invoke-RestMethod -Uri "https://api.github.com/repos/mozilla/geckodriver/releases/tags/v$Version" -Headers $headers
$asset = $rel.assets | Where-Object { $_.name -eq $name } | Select-Object -First 1
$apiDigest = ''
if ($asset -and $asset.digest) { $apiDigest = ($asset.digest -replace '^sha256:', '').ToLowerInvariant() }
Write-Host "release API digest $apiDigest"
if ($apiDigest -and $actual -ne $apiDigest) { throw "geckodriver zip sha256 $actual differs from the release API digest $apiDigest" }
$pinned = $ZipSha256.Trim().ToLowerInvariant()
if ($pinned) {
    if ($actual -ne $pinned) { throw "geckodriver zip sha256 $actual differs from the value pinned in the workflow $pinned" }
} else {
    if (-not $apiDigest) { throw 'no pinned geckodriver sha256 and the release API gives no digest: nothing to check against' }
    Write-Host "::warning::GECKODRIVER_WIN64_ZIP_SHA256 is empty in the workflow; only the release API digest was checked. Pin $actual"
}

Expand-Archive -LiteralPath $zip -DestinationPath $dir -Force
$gd = Join-Path $dir 'geckodriver.exe'
if (-not (Test-Path -LiteralPath $gd)) { throw "geckodriver.exe is not in $name" }
$versionLine = (& $gd --version | Select-Object -First 1)
if ($versionLine -notmatch [regex]::Escape("geckodriver $Version")) { throw "geckodriver says '$versionLine', expected $Version" }
$info = [ordered]@{
    version             = $Version
    version_line        = $versionLine
    url                 = $url
    zip_sha256          = $actual
    zip_sha256_pinned   = $pinned
    zip_sha256_api      = $apiDigest
    geckodriver_exe     = $gd
    geckodriver_sha256  = (Get-FileHash -Algorithm SHA256 -LiteralPath $gd).Hash.ToLowerInvariant()
}
$info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutDir 'geckodriver_info.json') -Encoding utf8
Write-Host ($info | ConvertTo-Json)
if ($env:GITHUB_ENV) { "GECKODRIVER_BIN=$gd" | Add-Content -Path $env:GITHUB_ENV -Encoding utf8 }
