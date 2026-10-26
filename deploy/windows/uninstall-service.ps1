<#
.SYNOPSIS
    Remove the PPT deck MCP service and its firewall rules. Leaves $InstallRoot on disk.

.EXAMPLE
    .\uninstall-service.ps1 -ServiceName PPTDeckMCP
#>
[CmdletBinding()]
param(
    [string]$ServiceName = 'PPTDeckMCP',
    [string]$NssmPath = 'C:\Tools\nssm\nssm.exe',
    [switch]$RemoveData
)

$ErrorActionPreference = 'Stop'

if (Test-Path $NssmPath) {
    & $NssmPath stop $ServiceName confirm 2>$null | Out-Null
    & $NssmPath remove $ServiceName confirm | Out-Null
} else {
    & sc.exe stop $ServiceName | Out-Null
    & sc.exe delete $ServiceName | Out-Null
}

Get-NetFirewallRule -DisplayName "ppt-mcp inbound ($ServiceName)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName 'ppt-mcp block soffice egress' -ErrorAction SilentlyContinue | Remove-NetFirewallRule

# Orphaned LibreOffice from an unclean shutdown (the Job Object gap): report, do not kill silently.
$stray = Get-Process soffice.bin -ErrorAction SilentlyContinue
if ($stray) {
    Write-Warning "$($stray.Count) soffice.bin still running. Stop them with: Get-Process soffice.bin | Stop-Process"
}

if ($RemoveData) {
    Write-Warning 'RemoveData was requested: delete the data directory yourself after checking retention requirements (Protected B disposal: MP-6).'
}

Write-Host 'Service removed.' -ForegroundColor Green
