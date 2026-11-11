<#
Outbound UDP blocked by one Windows Firewall rule, and the way back. The Windows counterpart of the Linux rule
`tc qdisc ... netem loss 100%` on eth0 (private study repo, run_in_container.sh / run_h3_in_container.sh).

What the Linux rule let through and what this rule lets through:

  Linux (tc, IPv4 UDP band, eth0)              Windows (this rule, all programs, all profiles)
  ------------------------------------------   ------------------------------------------------------
  all outbound IPv4 UDP dropped                all outbound UDP (IPv4 and IPv6) to remote ports 1-52 and 54-65535 blocked
  UDP port 53 dropped too                      UDP port 53 left OPEN
  names resolved over TCP (glibc use-vc)       names resolved by the Windows DNS client service, which speaks UDP/53 and has no
                                               TCP fallback on a timeout: with port 53 closed nothing would resolve
  TCP, loopback, ICMP untouched                same (the firewall does not filter loopback; no TCP or ICMP rule)

The remaining differences are measured, not hidden: verify_udp_block.py records whether a send into the block fails at once
(`block_mode` "send error": the firewall refuses the send) or vanishes ("silent drop": the Linux behaviour).

  -Action enable   turn all firewall profiles on, remove an old rule of the same name, add the rule, write the rule text and
                   the profile state to -OutFile
  -Action disable  remove the rule (also when it is absent); never fails
  -Action show     print the rule
#>
param(
    [Parameter(Mandatory)][ValidateSet('enable', 'disable', 'show')][string]$Action,
    [string]$OutFile = ''
)
$ErrorActionPreference = 'Stop'
$RuleName = 'retail-udp-blocked'
$Ports = '1-52,54-65535'

function Invoke-Netsh([string[]]$NetshArgs, [switch]$AllowFailure) {
    $out = & netsh @NetshArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) { throw "netsh $($NetshArgs -join ' ') failed ($LASTEXITCODE): $out" }
    return $out
}

function Remove-Rule { Invoke-Netsh @('advfirewall', 'firewall', 'delete', 'rule', "name=$RuleName") -AllowFailure | Out-Null }

switch ($Action) {
    'enable' {
        $profilesBefore = Invoke-Netsh @('advfirewall', 'show', 'allprofiles', 'state')
        Invoke-Netsh @('advfirewall', 'set', 'allprofiles', 'state', 'on') | Out-Null
        Remove-Rule
        $add = @('advfirewall', 'firewall', 'add', 'rule', "name=$RuleName", 'dir=out', 'action=block',
                 'protocol=UDP', "remoteport=$Ports", 'profile=any', 'enable=yes')
        Invoke-Netsh $add | Out-Null
        $text = "command: netsh $($add -join ' ')`r`n`r`nprofile state before:`r`n$profilesBefore`r`nprofile state now:`r`n" +
                (Invoke-Netsh @('advfirewall', 'show', 'allprofiles', 'state')) + "`r`nrule as stored:`r`n" +
                (Invoke-Netsh @('advfirewall', 'firewall', 'show', 'rule', "name=$RuleName", 'verbose'))
        Write-Host $text
        if ($OutFile) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
            Set-Content -LiteralPath $OutFile -Value $text -Encoding utf8
        }
    }
    'disable' {
        Remove-Rule
        Write-Host (Invoke-Netsh @('advfirewall', 'firewall', 'show', 'rule', "name=$RuleName") -AllowFailure)
    }
    'show' {
        Write-Host (Invoke-Netsh @('advfirewall', 'firewall', 'show', 'rule', "name=$RuleName", 'verbose') -AllowFailure)
    }
}
exit 0
