param([string]$Root = $PSScriptRoot, [switch]$AllowLegacyIdleStop, [int]$DrainTimeoutSec = 90, [int]$WmiTimeoutSec = 15)
# Stopfix 02.11.2026 (Nexus-Tools): 1) health_watch  2) Core (stop_nexus.ps1, Drain)  3) Gateway 8300  4) Pruefung
# Gezielt per CommandLine (Dateiname + Root), nichts Fremdes. Ollama bleibt.
# 2026-11-09: DrainTimeoutSec durchreichen (Restart-Hang = langer Drain bei laufender Arbeit).
# Inheritfix 16.11.2026: alle CIM/WMI-Aufrufe (Win32_Process, Get-NetTCPConnection) mit hartem Zeitlimit,
# Ausweichweg Get-Process + CommandLine je PID bzw. netstat; Zeitlimit -> lauter Logeintrag, nichts Fremdes beenden.
$ErrorActionPreference = "Stop"
$pwsh = (Get-Process -Id $PID).Path
$rootRx = [regex]::Escape([IO.Path]::GetFullPath($Root).TrimEnd('\'))
$ctlLog = Join-Path $Root "workspace\logs\nexus_control.log"
function CLog([string]$m) { try { Add-Content -LiteralPath $ctlLog -Value ("[{0}] stop_all: {1}" -f (Get-Date).ToString("s"), $m) -Encoding UTF8 } catch { }; Write-Host $m }  # Write-Host: verfaelscht keine Funktions-Rueckgaben
function Invoke-WithTimeout([scriptblock]$sb, [int]$sec, [object[]]$argsList = @()) {
  $ps = [PowerShell]::Create(); $h = $null
  try {
    $null = $ps.AddScript($sb.ToString()); foreach ($a in $argsList) { $null = $ps.AddArgument($a) }
    $h = $ps.BeginInvoke()
    if (-not $h.AsyncWaitHandle.WaitOne($sec * 1000)) { try { $null = $ps.BeginStop($null, $null) } catch { }; return @{ ok = $false; value = $null; error = "timeout ${sec}s" } }
    $v = @($ps.EndInvoke($h))
    if ($ps.HadErrors -and $v.Count -eq 0) { return @{ ok = $false; value = $null; error = ($ps.Streams.Error | Select-Object -First 1 | Out-String).Trim() } }
    return @{ ok = $true; value = $v; error = "" }
  } catch { return @{ ok = $false; value = $null; error = $_.Exception.Message } }
  finally { if (-not $h -or $h.IsCompleted) { $ps.Dispose() } }
}
# Liefert PIDs der NEXUS-Pythons mit $script in der CommandLine (unter $Root); $null = unbekannt (Zeitlimit/Fehler)
function Get-NexusPy([string]$script) {
  $sRx = [regex]::Escape($script)
  $r = Invoke-WithTimeout { param($t, $sRx, $rootRx) @(Get-CimInstance Win32_Process -OperationTimeoutSec $t -ErrorAction Stop | Where-Object {
      $_.Name -match '^pythonw?(\d|\.\d+)*\.exe$' -and $_.CommandLine -match $sRx -and $_.CommandLine -match $rootRx } | ForEach-Object { $_.ProcessId }) } ($WmiTimeoutSec + 5) @($WmiTimeoutSec, $sRx, $rootRx)
  if ($r.ok) { return ,@($r.value | Where-Object { $_ }) }
  CLog ("WARN WMI-Pruefung {0} fehlgeschlagen ({1}) -> Ausweichweg Get-Process + CommandLine je PID" -f $script, $r.error)
  $cands = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^pythonw?(\d|\.\d+)*$' } | ForEach-Object { $_.Id })
  $fb = Invoke-WithTimeout { param($ids, $sRx, $rootRx) foreach ($i in $ids) { $c = Get-CimInstance Win32_Process -Filter "ProcessId=$i" -OperationTimeoutSec 5 -ErrorAction SilentlyContinue; if ($c -and $c.CommandLine -match $sRx -and $c.CommandLine -match $rootRx) { $i } } } 20 @($cands, $sRx, $rootRx)
  if ($fb.ok) { return ,@($fb.value | Where-Object { $_ }) }
  CLog ("ERROR WMI-Zeitlimit: {0} nicht bestimmbar ({1}); nichts beendet" -f $script, $fb.error)
  return $null
}
function Get-ListenPorts([int[]]$ports) {
  $r = Invoke-WithTimeout { param($ports) @($ports | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue }) } 15 @(,$ports)
  if ($r.ok) { return ,@($r.value | Where-Object { $_ }) }
  CLog ("WARN Get-NetTCPConnection fehlgeschlagen ({0}) -> Ausweichweg netstat" -f $r.error)
  $ns = @(netstat -ano -p tcp 2>$null | Select-String 'LISTENING')
  return ,@($ports | Where-Object { $p = $_; $ns | Where-Object { $_.Line -match (":{0}\s" -f $p) } })
}
function Stop-NexusPy([string]$script, [string]$label) {
  $procs = Get-NexusPy $script
  if ($null -eq $procs) { CLog ("ERROR {0} nicht bestimmbar (WMI-Zeitlimit), Abbruch ohne Beenden" -f $label); exit 33 }
  foreach ($p in $procs) { Write-Output ("stop {0} pid={1}" -f $label, $p); Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
  $dl = (Get-Date).AddSeconds(15)
  while ($true) {
    $left = Get-NexusPy $script
    if ($null -eq $left) { CLog ("ERROR {0} Restpruefung nicht moeglich (WMI-Zeitlimit)" -f $label); exit 33 }
    if ($left.Count -eq 0) { break }
    if ((Get-Date) -ge $dl) { Write-Output ("ERROR {0} laeuft noch" -f $label); exit 31 }
    Start-Sleep -Milliseconds 300
  }
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
  $busy = Get-ListenPorts @(8299, 8443, 8300)
  if ($busy.Count -eq 0) { break }; Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $dl)
if ($busy.Count -gt 0) { Write-Output ("ERROR Ports noch belegt: {0}" -f ($busy -join ',')); exit 32 }
Write-Output "NEXUS komplett gestoppt (health_watch, Core 8299/8443, Gateway 8300)"
exit 0
