param([string]$Root = $PSScriptRoot, [switch]$AllowLegacyIdleStop, [int]$DrainTimeoutSec = 90)
$ErrorActionPreference = "Stop"
$env:TEMP = "G:\meine-ki\nexus-ai\workspace\tmp"; $env:TMP = $env:TEMP; New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null  # C: tabu (20261028)
# Hang-Fix 2026-11-09: Std-Handles vor Kindprozessen freigeben (sonst wartet Aufrufer ewig auf geerbte Pipes).
function Disable-NexusStdHandleInheritance {
  try {
    if (-not ("NexusStdHandlesRestart" -as [type])) {
      Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class NexusStdHandlesRestart {
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr GetStdHandle(int n);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetHandleInformation(IntPtr h, int mask, int flags);
  public static int Clear() { int ok = 0; foreach (int n in new int[]{-10,-11,-12}) { IntPtr h = GetStdHandle(n); if (h != IntPtr.Zero && h != new IntPtr(-1) && SetHandleInformation(h, 1, 0)) ok++; } return ok; }
}
"@
    }
    return [NexusStdHandlesRestart]::Clear()
  } catch { return -1 }
}
Write-Output ("restart: std_handle_inheritance_cleared={0}" -f (Disable-NexusStdHandleInheritance))
$pwsh = (Get-Process -Id $PID).Path
$stopArgs = @("-NoProfile", "-File", (Join-Path $Root "stop_nexus_all.ps1"), "-Root", $Root, "-DrainTimeoutSec", "$DrainTimeoutSec")
# stop_nexus_all -> stop_nexus.ps1; pass shorter drain via env for visibility
$env:NEXUS_DRAIN_TIMEOUT_SEC = "$DrainTimeoutSec"
if ($AllowLegacyIdleStop) { $stopArgs += "-AllowLegacyIdleStop" }
Write-Output ("restart: stop begin DrainTimeoutSec={0}" -f $DrainTimeoutSec)
& $pwsh @stopArgs
$rc = $LASTEXITCODE
if ($rc -ne 0) {
  Write-Output ("BLOCKED restart: safe stop did not complete, rc={0}; running work preserved" -f $rc)
  exit $rc
}
Write-Output "restart: start begin"
$null = Disable-NexusStdHandleInheritance
& $pwsh -NoProfile -File (Join-Path $Root "start_nexus_all.ps1") -Root $Root
$startRc = $LASTEXITCODE
# MH01 20261028: Health-Watchdog mitstarten (Einzelinstanz per Lock, BelowNormal, Logs auf G:)
try {
  $hwRun = @(Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "health_watch\.py" })
  if ($hwRun.Count -eq 0) {
    try { . (Join-Path $Root "nexus_python.ps1"); $hwPy = Get-NexusPython -Root $Root } catch { Write-Output ("ERROR health_watch nicht gestartet: {0}" -f $_.Exception.Message); if ($startRc -eq 0) { $startRc = 26 }; throw }
    $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0; PriorityClass = [uint32]16384 }
    $hwCmd = '"{0}" -B "{1}"' -f $hwPy, (Join-Path $Root 'nexus\health_watch.py')
    $hwr = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $hwCmd; CurrentDirectory = $Root; ProcessStartupInformation = $si }
    if ($hwr.ReturnValue -ne 0) { Write-Output ("ERROR health_watch Start ReturnValue={0}" -f $hwr.ReturnValue); if ($startRc -eq 0) { $startRc = 27 } }
    else { Write-Output ("health_watch gestartet pid={0}" -f $hwr.ProcessId) }
  }
} catch { Write-Output ("ERROR health_watch: {0}" -f $_.Exception.Message); if ($startRc -eq 0) { $startRc = 26 } }
Write-Output ("restart: done startRc={0}" -f $startRc)
exit $startRc