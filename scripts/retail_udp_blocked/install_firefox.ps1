<#
Install the retail (Mozilla-built) Firefox for Windows x64 of one version from Mozilla's release server and check it twice.

New for the Windows reference (no Linux counterpart; the Linux image unpacked the tarball and checked its sha512 against SHA512SUMS).

  1. download SHA256SUMS and "Firefox Setup <version>.msi" from archive.mozilla.org/pub/firefox/releases/<version>/
  2. the msi sha256 must equal the line Mozilla publishes for win64/en-US/Firefox Setup <version>.msi
     AND the value written in the workflow (-MsiSha256); either one differing fails the step
  3. silent msi install into <OutDir>\firefox (no shortcuts, no maintenance service, not the default browser)
  4. firefox.exe and xul.dll must equal the values written in the workflow (-ExeSha256, -XulSha256); the same files
     the update server ships in the complete MAR of that version
  5. record version, build ID, source stamp and every hash in <OutDir>\firefox_info.json; export FIREFOX_BIN
#>
param(
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$MsiSha256,
    [Parameter(Mandatory)][string]$ExeSha256,
    [Parameter(Mandatory)][string]$XulSha256,
    [Parameter(Mandatory)][string]$WorkDir,
    [Parameter(Mandatory)][string]$OutDir
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-Sha256([string]$Path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }

function Get-WithRetry([string]$Uri, [string]$OutFile) {
    for ($i = 1; $i -le 4; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $OutFile -TimeoutSec 600; return }
        catch { if ($i -eq 4) { throw }; Write-Host "download failed ($i): $($_.Exception.Message)"; Start-Sleep -Seconds (5 * $i) }
    }
}

$base = "https://archive.mozilla.org/pub/firefox/releases/$Version"
$dl = Join-Path $WorkDir 'dl'
New-Item -ItemType Directory -Force -Path $dl | Out-Null

$sums = Join-Path $dl 'SHA256SUMS'
Get-WithRetry "$base/SHA256SUMS" $sums
$msiName = "Firefox Setup $Version.msi"
$key = "win64/en-US/$msiName"
$line = Get-Content -LiteralPath $sums | Where-Object { $_ -match '^[0-9a-f]{64}\s+\*?(.+)$' -and $Matches[1].Trim() -eq $key } | Select-Object -First 1
if (-not $line) { throw "SHA256SUMS of $Version has no line for $key" }
$published = ($line -split '\s+')[0].ToLowerInvariant()

$msi = Join-Path $dl 'firefox.msi'
Get-WithRetry "$base/win64/en-US/$([uri]::EscapeDataString($msiName))" $msi
$actual = Get-Sha256 $msi
Write-Host "msi sha256        $actual"
Write-Host "published sha256  $published"
Write-Host "workflow sha256   $($MsiSha256.ToLowerInvariant())"
if ($actual -ne $published) { throw "msi sha256 $actual differs from the SHA256SUMS line $published" }
if ($actual -ne $MsiSha256.ToLowerInvariant()) { throw "msi sha256 $actual differs from the value pinned in the workflow $MsiSha256" }

$dir = Join-Path $WorkDir 'firefox'
$log = Join-Path $OutDir 'msi_install.log'
function Invoke-Msi([string[]]$MsiArgs) {
    $p = Start-Process -FilePath msiexec.exe -ArgumentList $MsiArgs -Wait -PassThru
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) { throw "msiexec exited with $($p.ExitCode); see $log" }
}
$method = 'msi install'
Invoke-Msi @('/i', $msi, '/qn', '/norestart', '/l*v', $log, "INSTALL_DIRECTORY_PATH=$dir",
    'DESKTOP_SHORTCUT=false', 'TASKBAR_SHORTCUT=false', 'START_MENU_SHORTCUT=false',
    'INSTALL_MAINTENANCE_SERVICE=false', 'REGISTER_DEFAULT_BROWSER=false', 'PREVENT_REBOOT_REQUIRED=true')
$exe = Get-ChildItem -LiteralPath $dir -Recurse -Filter firefox.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) {
    # Administrative extraction of the same msi: files only, nothing registered.
    $method = 'msi administrative extraction'
    Invoke-Msi @('/a', $msi, '/qn', '/norestart', '/l*v', $log, "TARGETDIR=$dir")
    $exe = Get-ChildItem -LiteralPath $dir -Recurse -Filter firefox.exe | Select-Object -First 1
}
if (-not $exe) { throw "no firefox.exe under $dir after $method" }
$fxDir = $exe.DirectoryName
$xul = Join-Path $fxDir 'xul.dll'
$exeHash = Get-Sha256 $exe.FullName
$xulHash = Get-Sha256 $xul
Write-Host "firefox.exe sha256 $exeHash"
Write-Host "xul.dll sha256     $xulHash"
if ($exeHash -ne $ExeSha256.ToLowerInvariant()) { throw "firefox.exe sha256 $exeHash differs from the pinned $ExeSha256" }
if ($xulHash -ne $XulSha256.ToLowerInvariant()) { throw "xul.dll sha256 $xulHash differs from the pinned $XulSha256" }

$ini = @{}
Get-Content -LiteralPath (Join-Path $fxDir 'application.ini') | ForEach-Object { if ($_ -match '^(\w+)=(.*)$') { $ini[$Matches[1]] = $Matches[2] } }
if ($ini['Version'] -ne $Version) { throw "application.ini says version $($ini['Version']), expected $Version" }

$info = [ordered]@{
    version              = $ini['Version']
    build_id             = $ini['BuildID']
    source_stamp         = $ini['SourceStamp']
    source_repository    = $ini['SourceRepository']
    install_method       = $method
    firefox_exe          = $exe.FullName
    firefox_exe_sha256   = $exeHash
    xul_dll_sha256       = $xulHash
    msi_sha256           = $actual
    msi_sha256_published = $published
    msi_url              = "$base/win64/en-US/$msiName"
    sha256sums_url       = "$base/SHA256SUMS"
}
$info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutDir 'firefox_info.json') -Encoding utf8
Write-Host ($info | ConvertTo-Json)
if ($env:GITHUB_ENV) { "FIREFOX_BIN=$($exe.FullName)" | Add-Content -Path $env:GITHUB_ENV -Encoding utf8 }
