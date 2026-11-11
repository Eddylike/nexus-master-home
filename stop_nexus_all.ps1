param([string]$Root = $PSScriptRoot, [switch]$AllowLegacyIdleStop, [int]$DrainTimeoutSec = 90)
# Stopfix 02.11.2026 (Nexus-Tools): 1) health_watch  2) Core (stop_nexus.ps1, Drain)  3) Gateway 8300  4) Pruefung
# Gezielt per CommandLine (Dateiname + Root), nichts Fremdes. Ollama bleibt.
# 2026-11-09: DrainTimeoutSec durchreichen (Restart-Hang = langer Drain bei laufender Arbeit).
$ErrorActionPreference = "Stop"
$pwsh = (Get-Process -Id $PID).Path
$rootRx = [regex]::Escape([IO.Path]::GetFullPath($Root).TrimEnd('\'))
function Get-NexusPy([string]$script) {
  @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match '^pythonw?(\d|\.\d+)*\.exe$' -and $_.CommandLine -match ([regex]::Escape($script)) -and $_.CommandLine -match $rootRx })
}
function Stop-NexusPy([string]$script, [string]$label) {
  $procs = Get-NexusPy $script
  foreach ($p in $procs) { Write-Output ("stop {0} pid={1}" -f $label, $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  $dl = (Get-Date).AddSeconds(15)
  while ((Get-NexusPy $script).Count -gt 0 -and (Get-Date) -lt $dl) { Start-Sleep -Milliseconds 300 }
  if ((Get-NexusPy $script).Count -gt 0) { Write-Output ("ERROR {0} laeuft noch" -f $label); exit 31 }
}
# 1) health_watch zuerst, damit er den Core nicht wieder hochzieht
Stop-NexusPy 'health_watch.py' 'health_watch'
# 2) Core mit Drain (blockiert bei laufender Arbeit -> rc != 0, Gateway bleibt dann stehen)
$stopArgs = @("-NoProfile", "-File", (Join-Path $Root "stop_nexus.ps1"), "-Root", $Root, "-DrainTimeoutSec", "$DrainTimeoutSec")
if ($AllowLegacyIdleStop) { $stopArgs += "-AllowLegacyIdleStop" }
Write-Output ("stop_all: drain timeout={0}s" -f $DrainTimeoutSec)
& $pwsh @stopArgs
$rc = $LASTEXITCODE
if ($rc -ne 0) { Write-Output ("ERROR core stop rc={0}; health_watch ist gestoppt, Gateway bleibt" -f $rc); exit $rc }
# 3) Gateway 8300
Stop-NexusPy 'gateway_allowlist.py' 'gateway'
# 4) Pruefung
$busy = @()
$dl = (Get-Date).AddSeconds(15)
do {
  $busy = @(8299, 8443, 8300 | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue })
  if ($busy.Count -eq 0) { break }; Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $dl)
if ($busy.Count -gt 0) { Write-Output ("ERROR Ports noch belegt: {0}" -f ($busy -join ',')); exit 32 }
Write-Output "NEXUS komplett gestoppt (health_watch, Core 8299/8443, Gateway 8300)"
exit 0