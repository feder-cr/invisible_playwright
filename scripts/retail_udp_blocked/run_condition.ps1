<#
One condition of the measurement: proof of the condition, the measurements, proof again.

New for the Windows reference; it plays the part of run_h3_in_container.sh and run_in_container.sh of the Linux reference
(private study repo): the same measurements one after the other, with the proof of the condition before them (as there)
and, new here, after them. Every command line is appended to <OutDir>\commands.txt. A non-zero exit of any command stops the step.

  -Name control      outbound UDP free; 5 WebRTC gather sessions, 5+5 sessions against a TURN server of ours (address literal / name), then 5 sessions x 3 sites x 3 HTTP/3 visits   (UDP_BLOCK=0)
  -Name blocked      outbound UDP blocked by udp_block.ps1 -Action enable, which the workflow ran just before; same measurements (UDP_BLOCK=1)
  -Name doh-free     UDP free, Firefox forced to DNS over HTTPS (network.trr.mode=3); the HTTP/3 visits only
  -Name doh-blocked  UDP blocked, Firefox forced to DNS over HTTPS; the HTTP/3 visits only

The DoH provider is the one the product declares for every session (invisible_core, src/invisible_core/prefs.py: network.trr.uri
and network.trr.bootstrapAddr, both set as user values); the product's engine turns network.trr.mode to 3 behind a proxy, and
here it is set to 3 directly. h3_fallback.py reads the browser's DNS state at the end of each session (--require-doh): if it does
not show DoH in use, the step fails instead of measuring native name resolution.
#>
param(
    [Parameter(Mandatory)][ValidateSet('control', 'doh-free', 'blocked', 'doh-blocked')][string]$Name,
    [Parameter(Mandatory)][string]$OutDir,
    [int]$Runs = 5,
    [int]$Visits = 3,
    [int]$WaitSeconds = 60
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$network = if ($Name -like '*blocked') { 'blocked' } else { 'control' }
$doh = $Name -like 'doh-*'
$cond = Join-Path $OutDir $Name
New-Item -ItemType Directory -Force -Path $cond | Out-Null
$env:UDP_BLOCK = if ($network -eq 'blocked') { '1' } else { '0' }
$env:MOZ_CRASHREPORTER_DISABLE = '1'

function Invoke-Logged([string]$Script, [string[]]$PyArgs) {
    $line = "python scripts/retail_udp_blocked/$Script $($PyArgs -join ' ')"
    Add-Content -LiteralPath (Join-Path $OutDir 'commands.txt') -Value "[$Name] $line" -Encoding utf8
    Write-Host "::group::$line"
    & python (Join-Path $here $Script) @PyArgs
    $code = $LASTEXITCODE
    Write-Host '::endgroup::'
    if ($code -ne 0) { throw "$Script exited with $code" }
}

Invoke-Logged 'verify_udp_block.py' @('--expect', $network, '--out', (Join-Path $cond 'udp_block_check.json'))
if (-not $doh) {
    Invoke-Logged 'webrtc_gather.py' @('--runs', "$Runs", '--wait', "$WaitSeconds", '--label', 'webrtc', '--out', $cond)
    if ($Name -eq 'control') {
        # One question the STUN page cannot answer: on a host with ONE IPv4 interface, how many TURN-over-TCP allocations does a retail
        # Firefox attempt, and when. A TURN server of ours over UDP on the runner's own address (nothing listens on the TCP port), the
        # address as a literal and as a name; 25 s is enough (the errors come within 3 s, the gathering ends or it does not).
        # The TURN server is a Python process listening on the runner's own address: the Windows Firewall may refuse its inbound UDP,
        # so an allow rule for that port is added for the length of the step (and the server says how many datagrams it heard).
        netsh advfirewall firewall add rule name="retail-measure-turn" dir=in action=allow protocol=UDP localport=3478 | Out-Null
        try {
            Invoke-Logged 'webrtc_gather.py' @('--runs', "$Runs", '--wait', '25', '--label', 'webrtc-turn', '--target', 'turn', '--out', $cond)
        } finally {
            netsh advfirewall firewall delete rule name="retail-measure-turn" | Out-Null
        }
    }
}
$h3 = @('--runs', "$Runs", '--visits', "$Visits", '--label', 'h3', '--out', $cond)
if ($doh) {
    $h3 += @('--pref', 'network.trr.mode=3',
             '--pref', 'network.trr.uri=https://mozilla.cloudflare-dns.com/dns-query',
             '--pref', 'network.trr.bootstrapAddr=162.159.61.4',
             '--require-doh')
}
Invoke-Logged 'h3_fallback.py' $h3
Invoke-Logged 'verify_udp_block.py' @('--expect', $network, '--out', (Join-Path $cond 'udp_block_check_after.json'))
