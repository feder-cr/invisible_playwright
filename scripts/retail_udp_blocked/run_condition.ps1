<#
One condition of the measurement: proof of the condition, 5 WebRTC gather sessions, 5 sessions x 3 sites of HTTP/3 visits, proof again.

New for the Windows reference; it plays the part of run_h3_in_container.sh and run_in_container.sh of the Linux reference
(private study repo): the same two measurements one after the other, with the proof of the condition before them (as there)
and, new here, after them. Every command line is appended to <OutDir>\commands.txt. A non-zero exit of any command stops the step.

  -Condition control   outbound UDP free   (UDP_BLOCK=0)
  -Condition blocked   outbound UDP blocked by udp_block.ps1 -Action enable, which the workflow ran just before (UDP_BLOCK=1)
#>
param(
    [Parameter(Mandatory)][ValidateSet('control', 'blocked')][string]$Condition,
    [Parameter(Mandatory)][string]$OutDir,
    [int]$Runs = 5,
    [int]$Visits = 3,
    [int]$WaitSeconds = 60
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$cond = Join-Path $OutDir $Condition
New-Item -ItemType Directory -Force -Path $cond | Out-Null
$env:UDP_BLOCK = if ($Condition -eq 'blocked') { '1' } else { '0' }
$env:MOZ_CRASHREPORTER_DISABLE = '1'

function Invoke-Logged([string]$Script, [string[]]$PyArgs) {
    $line = "python scripts/retail_udp_blocked/$Script $($PyArgs -join ' ')"
    Add-Content -LiteralPath (Join-Path $OutDir 'commands.txt') -Value "[$Condition] $line" -Encoding utf8
    Write-Host "::group::$line"
    & python (Join-Path $here $Script) @PyArgs
    $code = $LASTEXITCODE
    Write-Host '::endgroup::'
    if ($code -ne 0) { throw "$Script exited with $code" }
}

Invoke-Logged 'verify_udp_block.py' @('--expect', $Condition, '--out', (Join-Path $cond 'udp_block_check.json'))
Invoke-Logged 'webrtc_gather.py' @('--runs', "$Runs", '--wait', "$WaitSeconds", '--label', 'webrtc', '--out', $cond)
Invoke-Logged 'h3_fallback.py' @('--runs', "$Runs", '--visits', "$Visits", '--label', 'h3', '--out', $cond)
Invoke-Logged 'verify_udp_block.py' @('--expect', $Condition, '--out', (Join-Path $cond 'udp_block_check_after.json'))
