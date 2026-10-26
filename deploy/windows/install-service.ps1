<#
.SYNOPSIS
    Install the PPT deck MCP server as a Windows service (NSSM + venv), for a native
    Windows Server deployment with no containers.

.DESCRIPTION
    Creates (idempotently) a Python venv, installs requirements, and registers the service with NSSM.
    Also applies the firewall rules and ACLs the deployment needs. Nothing here writes secrets: put
    them in the service environment afterwards (see deploy/windows/ppt-mcp.env.example).

.PARAMETER InstallRoot
    Where the server lives, e.g. C:\Services\ppt-mcp. Logs and data go beside it.
.PARAMETER ServiceName
    Windows service name (default PPTDeckMCP).
.PARAMETER ServiceAccount
    Account to run as, e.g. "CONTOSO\svc-ppt-mcp". Omit to run as LocalService (recommended when a
    gMSA is not available). For a gMSA use `sc.exe config <name> obj= "CONTOSO\svc$"` — NSSM's
    ObjectName cannot set one without a password.
.PARAMETER LibreChatHost
    The only remote address allowed to reach the MCP port (IP or CIDR). Required for Protected B.

.EXAMPLE
    .\install-service.ps1 -InstallRoot C:\Services\ppt-mcp -ServiceName PPTDeckMCP `
        -ServiceAccount "CONTOSO\svc-ppt-mcp" -LibreChatHost 10.20.0.15
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallRoot,
    [string]$ServiceName = 'PPTDeckMCP',
    [string]$ServiceAccount,
    [string]$LibreChatHost,
    [string]$Python = 'py -3.12',
    [int]$Port = 8931,
    [int]$PreviewWidth = 1600,
    [string]$PublicBase = '',
    [string]$NssmPath = 'C:\Tools\nssm\nssm.exe'
)

$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Error $m; exit 1 }

if (-not (Test-Path $NssmPath)) { Fail "nssm.exe not found at $NssmPath (download from nssm.cc, use the win64 build)" }
$nssm = $NssmPath
$root = (Resolve-Path -LiteralPath (New-Item -ItemType Directory -Force -Path $InstallRoot)).Path
$venv = Join-Path $root '.venv'
$py   = Join-Path $venv 'Scripts\python.exe'
$logs = Join-Path $root 'logs'
$data = Join-Path $root 'data'
$server = Join-Path $root 'server.py'

foreach ($d in @($logs, $data, (Join-Path $data 'previews'), (Join-Path $data 'uploads\decks'))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

if (-not (Test-Path $server)) { Fail "$server not found — copy the ppt-deck-mcp repo to $root first" }

# ---------------------------------------------------------------- 1. venv + dependencies
if (-not (Test-Path $py)) {
    Step "Creating venv in $venv"
    & cmd /c "$Python -m venv `"$venv`"" || Fail "venv creation failed"
}
Step 'Installing requirements (pinned by requirements.txt)'
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r (Join-Path $root 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed — check the private wheelhouse/proxy settings' }

# pyuno check: the render bridge needs an interpreter that can `import uno`.
$soffice = $null
foreach ($candidate in @(
    "$env:ProgramFiles\LibreOffice\program\soffice.exe",
    "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe",
    'C:\Tools\LibreOfficePortable\App\libreoffice\program\soffice.exe')) {
    if (Test-Path $candidate) { $soffice = $candidate; break }
}
$unoPython = ''
if ($soffice) {
    $loPython = Join-Path (Split-Path $soffice) 'python.exe'
    if (Test-Path $loPython) {
        & $loPython -c 'import uno' 2>$null
        if ($LASTEXITCODE -eq 0) { $unoPython = $loPython }
    }
}
if ($unoPython) {
    Step "pyuno OK: $unoPython (warm renderer enabled)"
} else {
    Write-Warning 'No LibreOffice python with pyuno found — previews will use the cold PDF chain (slower). Set PPT_UNO_PYTHON later if you install LibreOffice.'
}

# ---------------------------------------------------------------- 2. service registration
Step "Registering service $ServiceName"
& sc.exe query $ServiceName *> $null
if ($LASTEXITCODE -eq 0) {
    Step 'Service exists — stopping and re-registering'
    & $nssm stop $ServiceName confirm 2>$null | Out-Null
    & $nssm remove $ServiceName confirm | Out-Null
    Start-Sleep -Seconds 1
}
& $nssm install $ServiceName $py "-u `"$server`" --transport http --host 0.0.0.0 --port $Port"
& $nssm set $ServiceName AppDirectory $root
& $nssm set $ServiceName DisplayName 'PPT deck MCP server'
& $nssm set $ServiceName Description 'Deck Studio MCP server: builds .pptx and renders slide previews (LibreOffice).'
& $nssm set $ServiceName Start SERVICE_AUTO_START

# Logs: rotated, timestamped, flushed (python -u keeps stdout unbuffered).
& $nssm set $ServiceName AppStdout (Join-Path $logs 'ppt-mcp.out.log')
& $nssm set $ServiceName AppStderr (Join-Path $logs 'ppt-mcp.err.log')
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateOnline 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName AppTimestampLog 1

# Restart policy and a graceful stop window before NSSM kills the process tree.
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppRestartDelay 5000
& $nssm set $ServiceName AppThrottle 5000
& $nssm set $ServiceName AppStopMethodConsole 15000
& $nssm set $ServiceName AppStopMethodThreads 15000

# Environment. Add secrets (DLP/entra client ids etc.) afterwards via `nssm set <name> AppEnvironmentExtra`.
$envPairs = @(
    'PYTHONUNBUFFERED=1',
    'PYTHONIOENCODING=utf-8',
    'PYTHONUTF8=1',
    "PPT_OUTPUT_DIR=$data",
    "PPT_UPLOAD_DIR=$(Join-Path $data 'uploads\decks')",
    "PPT_PREVIEW_WIDTH=$PreviewWidth",
    'PPT_RENDER_CACHE=1',
    'PPT_WARM_RENDER=1',
    'PPT_RENDER_QUEUE_MAX=256',
    'PPT_RENDER_TIMEOUT_S=180'
)
if ($PublicBase) { $envPairs += "PPT_PUBLIC_BASE=$PublicBase" }
if ($unoPython)  { $envPairs += "PPT_UNO_PYTHON=$unoPython" }
if ($soffice)    { $envPairs += "SOFFICE_BIN=$soffice" }
& $nssm set $ServiceName AppEnvironmentExtra @envPairs

if ($ServiceAccount) {
    Step "Service account: $ServiceAccount"
    $cred = Get-Credential -UserName $ServiceAccount -Message "Password for $ServiceAccount (Log on as a service)"
    & $nssm set $ServiceName ObjectName $ServiceAccount $cred.GetNetworkCredential().Password
} else {
    Write-Host '    (running as LocalService; grant it modify rights on the data directory below)'
}

# ---------------------------------------------------------------- 3. filesystem ACLs
Step 'Hardening data directory ACLs'
& icacls $data /inheritance:r | Out-Null
$who = if ($ServiceAccount) { $ServiceAccount } else { 'NT AUTHORITY\LOCAL SERVICE' }
& icacls $data /grant:r "${who}:(OI)(CI)M" | Out-Null
& icacls $data /grant:r 'BUILTIN\Administrators:(OI)(CI)F' | Out-Null
& icacls "$logs" /inheritance:r | Out-Null
& icacls "$logs" /grant:r "${who}:(OI)(CI)M" | Out-Null
& icacls "$logs" /grant:r 'BUILTIN\Administrators:(OI)(CI)F' | Out-Null

# ---------------------------------------------------------------- 4. firewall
if ($LibreChatHost) {
    Step "Firewall: allow $Port only from $LibreChatHost"
    Get-NetFirewallRule -DisplayName "ppt-mcp inbound ($ServiceName)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "ppt-mcp inbound ($ServiceName)" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -RemoteAddress $LibreChatHost -Profile Domain,Private | Out-Null
    Write-Host '    Reminder: do NOT open this port to the LAN; the browser never talks to it directly.'
}
if ($soffice) {
    Step 'Firewall: block LibreOffice egress (untrusted .pptx must not reach the network)'
    Get-NetFirewallRule -DisplayName 'ppt-mcp block soffice egress' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName 'ppt-mcp block soffice egress' -Direction Outbound -Action Block `
        -Program $soffice -Profile Domain,Private,Public | Out-Null
}

# ---------------------------------------------------------------- 5. start + verify
Step 'Starting service'
& $nssm start $ServiceName
Start-Sleep -Seconds 5
& sc.exe query $ServiceName

Step 'Health check'
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/apps/deck_studio/app.js" -UseBasicParsing -TimeoutSec 10
    Write-Host "    HTTP $($r.StatusCode) — server is up" -ForegroundColor Green
} catch {
    Write-Warning "Health check failed: $($_.Exception.Message). Check $logs\ppt-mcp.err.log"
}

Write-Host @"

Next steps
  1. Copy the deployment skills:  robocopy skills "$root\skill" /E
     and point LibreChat at them (see skills/README.md).
  2. Apply deploy/librechat/mcpServers.ppt.yaml to librechat.yaml and restart LibreChat
     (it caches MCP tool lists, so new or renamed tools need the restart).
  3. Open the Deck Studio pane once and confirm the slide images load — that proves
     PPT_PUBLIC_BASE is reachable from the *browser*, not just from the server.
  4. Kill soffice.bin with Task Manager while a preview is running: the next preview must
     recover. Then do the same for the bridge python process and check the process tree
     in Task Manager — see docs/windows-service.md ("Job Object" gap).
"@ -ForegroundColor DarkGray
