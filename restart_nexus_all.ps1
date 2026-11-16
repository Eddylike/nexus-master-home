param([string]$Root = $PSScriptRoot, [switch]$AllowLegacyIdleStop, [int]$DrainTimeoutSec = 90, [int]$StartTimeoutSec = 240, [int]$WmiTimeoutSec = 20)
# Restartfix 16.11.2026 (Nexus-Tools): jeder Schritt mit Zeitstempel in workspace\logs\restart_nexus_all.log,
# Stop/Start als Kindprozess mit Datei-Umleitung + Zeitlimit (keine geerbten Pipes), WMI nur mit Zeitlimit,
# health_watch per Start-Process -WindowStyle Hidden mit Logdateien auf G:. Fehler -> laut abbrechen (rc != 0).
$ErrorActionPreference = "Stop"
$env:TEMP = "G:\meine-ki\nexus-ai\workspace\tmp"; $env:TMP = $env:TEMP; New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null  # C: tabu (20261028)
$logDir = Join-Path $Root "workspace\logs"; New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$rlog = Join-Path $logDir "restart_nexus_all.log"
$runId = "{0}-{1}" -f (Get-Date -f "yyyyMMdd_HHmmss"), $PID
function RLog([string]$m) {
  $line = "[{0}] [{1}] {2}" -f (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff"), $runId, $m
  try { Add-Content -LiteralPath $rlog -Value $line -Encoding UTF8 } catch { }
  Write-Host $m  # Write-Host: verfaelscht keine Funktions-Rueckgaben (rc)
}
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
# Skriptblock in eigenem Runspace mit hartem Zeitlimit (fuer WMI/CIM). Rueckgabe: @{ok; timeout; value; error}
function Invoke-WithTimeout([scriptblock]$sb, [int]$sec, [object[]]$argsList = @()) {
  $ps = [PowerShell]::Create()
  try {
    $null = $ps.AddScript($sb.ToString()); foreach ($a in $argsList) { $null = $ps.AddArgument($a) }
    $h = $ps.BeginInvoke()
    if (-not $h.AsyncWaitHandle.WaitOne($sec * 1000)) {
      try { $null = $ps.BeginStop($null, $null) } catch { }
      return @{ ok = $false; timeout = $true; value = $null; error = "timeout ${sec}s" }
    }
    $v = @($ps.EndInvoke($h))
    if ($ps.HadErrors -and $v.Count -eq 0) { return @{ ok = $false; timeout = $false; value = $null; error = ($ps.Streams.Error | Select-Object -First 1 | Out-String).Trim() } }
    return @{ ok = $true; timeout = $false; value = $v; error = "" }
  } catch { return @{ ok = $false; timeout = $false; value = $null; error = $_.Exception.Message } }
  finally { if (-not $h -or $h.IsCompleted) { $ps.Dispose() } }
}
# Kind-Skript mit Datei-Umleitung und Zeitlimit; Ausgabe landet im Restart-Log. Rueckgabe: rc (124 = Zeitlimit)
function Invoke-ChildScript([string]$label, [string[]]$argList, [int]$timeoutSec) {
  $out = Join-Path $logDir ("restart_{0}.out.log" -f $label); $err = Join-Path $logDir ("restart_{0}.err.log" -f $label)
  $null = Disable-NexusStdHandleInheritance
  $p = Start-Process -FilePath $pwsh -ArgumentList $argList -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
  $null = $p.Handle
  RLog ("{0}: pid={1} timeout={2}s" -f $label, $p.Id, $timeoutSec)
  $done = $p.WaitForExit($timeoutSec * 1000)
  foreach ($f in @($out, $err)) { if (Test-Path -LiteralPath $f) { Get-Content -LiteralPath $f -ErrorAction SilentlyContinue | Where-Object { $_.Trim() } | ForEach-Object { RLog ("  {0}| {1}" -f $label, $_) } } }
  if (-not $done) { RLog ("ERROR {0}: Zeitlimit {1}s ueberschritten, pid={2} laeuft noch (nicht beendet)" -f $label, $timeoutSec, $p.Id); return 124 }
  $p.WaitForExit(); $rc = $p.ExitCode; if ($null -eq $rc) { $rc = 1 }
  return [int]$rc
}
$t0 = Get-Date
RLog ("restart: begin pid={0} DrainTimeoutSec={1} StartTimeoutSec={2} WmiTimeoutSec={3}" -f $PID, $DrainTimeoutSec, $StartTimeoutSec, $WmiTimeoutSec)
RLog ("restart: std_handle_inheritance_cleared={0}" -f (Disable-NexusStdHandleInheritance))
$pwsh = (Get-Process -Id $PID).Path
$env:NEXUS_DRAIN_TIMEOUT_SEC = "$DrainTimeoutSec"
# 1) Stop (health_watch, Core mit Drain, Gateway)
$stopArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f (Join-Path $Root "stop_nexus_all.ps1")), "-Root", ('"{0}"' -f $Root), "-DrainTimeoutSec", "$DrainTimeoutSec")
if ($AllowLegacyIdleStop) { $stopArgs += "-AllowLegacyIdleStop" }
RLog "restart: stop begin"
$rc = Invoke-ChildScript "stop" $stopArgs ($DrainTimeoutSec + 120)
RLog ("restart: stop end rc={0}" -f $rc)
if ($rc -ne 0) {
  RLog ("BLOCKED restart: safe stop did not complete, rc={0}; running work preserved" -f $rc)
  exit $rc
}
# 2) Start (Ollama-Reuse, Core, Gateway)
RLog "restart: start begin"
$startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f (Join-Path $Root "start_nexus_all.ps1")), "-Root", ('"{0}"' -f $Root))
$startRc = Invoke-ChildScript "start" $startArgs $StartTimeoutSec
RLog ("restart: start end rc={0}" -f $startRc)
# 3) health_watch (MH01 20261028: Einzelinstanz per Lock, BelowNormal, Logs auf G:)
RLog "restart: health_watch check begin"
$hwCheck = Invoke-WithTimeout { param($t) @(Get-CimInstance Win32_Process -Filter "name='python.exe' or name='pythonw.exe'" -OperationTimeoutSec $t -ErrorAction Stop | Where-Object { $_.CommandLine -match "health_watch\.py" } | ForEach-Object { $_.ProcessId }) } ($WmiTimeoutSec + 5) @($WmiTimeoutSec)
$hwPids = @()
if ($hwCheck.ok) {
  $hwPids = @($hwCheck.value | Where-Object { $_ })
  RLog ("restart: health_watch check via CIM ok running={0} pids={1}" -f $hwPids.Count, ($hwPids -join ","))
} else {
  RLog ("WARN restart: CIM-Pruefung fehlgeschlagen ({0}) -> Ausweichweg Get-Process + CommandLine je PID" -f $hwCheck.error)
  $cands = @(Get-Process -Name python, pythonw -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  $fb = Invoke-WithTimeout { param($ids) foreach ($i in $ids) { $c = Get-CimInstance Win32_Process -Filter "ProcessId=$i" -OperationTimeoutSec 5 -ErrorAction SilentlyContinue; if ($c -and $c.CommandLine -match "health_watch\.py") { $i } } } 15 @(,$cands)
  if ($fb.ok) { $hwPids = @($fb.value | Where-Object { $_ }); RLog ("restart: health_watch check via Fallback ok running={0} pids={1}" -f $hwPids.Count, ($hwPids -join ",")) }
  else { RLog ("WARN restart: Fallback-Pruefung ebenfalls fehlgeschlagen ({0}); starte trotzdem, Lockdatei verhindert Doppelinstanz" -f $fb.error) }
}
if ($hwPids.Count -eq 0) {
  try {
    . (Join-Path $Root "nexus_python.ps1"); $hwPy = Get-NexusPython -Root $Root
    RLog ("restart: health_watch python={0}" -f $hwPy)
    $hwOut = Join-Path $logDir "health_watch.stdout.log"; $hwErr = Join-Path $logDir "health_watch.stderr.log"
    $null = Disable-NexusStdHandleInheritance
    $hw = Start-Process -FilePath $hwPy -ArgumentList @("-B", ('"{0}"' -f (Join-Path $Root 'nexus\health_watch.py'))) -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $hwOut -RedirectStandardError $hwErr
    $null = $hw.Handle
    try { $hw.PriorityClass = 'BelowNormal' } catch { }
    Start-Sleep -Seconds 3
    $hw.Refresh()
    if ($hw.HasExited) { throw ("health_watch sofort beendet rc={0} (Lock belegt oder Fehler, siehe {1})" -f $hw.ExitCode, $hwErr) }
    RLog ("restart: health_watch gestartet pid={0} via Start-Process" -f $hw.Id)
  } catch {
    RLog ("ERROR restart: health_watch nicht gestartet: {0}" -f $_.Exception.Message)
    if ($startRc -eq 0) { $startRc = 26 }
  }
} else { RLog ("restart: health_watch laeuft bereits pids={0}" -f ($hwPids -join ",")) }
RLog ("restart: done startRc={0} dauer={1:N1}s" -f $startRc, ((Get-Date) - $t0).TotalSeconds)
exit $startRc
