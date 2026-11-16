# NEXUS AI Master - start on :8299 (Single-Instance)
param(
  [string]$Root = $PSScriptRoot,
  [int]$Port = 8299,
  [string]$HostAddr = "0.0.0.0"
)
$ErrorActionPreference = "Stop"
if (-not $env:NEXUS_BRIDGE_PROCESS_LEVEL) { $env:NEXUS_BRIDGE_PROCESS_LEVEL = "SAFE_WRITE" }
$runtimeTmp = Join-Path $Root "workspace\tmp"
New-Item -ItemType Directory -Force -Path $runtimeTmp | Out-Null
$env:TEMP = $runtimeTmp
$env:TMP = $runtimeTmp
$env:PYTHONDONTWRITEBYTECODE = '1'
$logDir = Join-Path $Root "workspace\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "nexus_start.log"
$pidFile = Join-Path $logDir "nexus.pid"
$lockFile = Join-Path $logDir "nexus_start.lock"
$server = Join-Path $Root "run_server.py"
# Core health includes several local model checks (14 s observed in 006B).
$coreHealthTimeoutSec = 30
$readinessBudgetSec = 45

# t329u: Kindprozesse (Server/Gateway) duerfen die Std-Handles des Aufrufers NICHT erben.
# Sonst haelt der dauerlaufende Server eine geerbte Pipe offen und jeder Aufrufer, der die
# Ausgabe liest (Pipeline, Remote-Shell, Scheduler), wartet ewig -> "haengender" Neustart.
function Disable-NexusStdHandleInheritance {
  try {
    if (-not ('NexusStdHandles' -as [type])) {
      Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class NexusStdHandles {
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr GetStdHandle(int n);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetHandleInformation(IntPtr h, int mask, int flags);
  public static int Clear() { int ok = 0; foreach (int n in new int[]{-10,-11,-12}) { IntPtr h = GetStdHandle(n); if (h != IntPtr.Zero && h != new IntPtr(-1) && SetHandleInformation(h, 1, 0)) ok++; } return ok; }
}
"@
    }
    return [NexusStdHandles]::Clear()
  } catch { return -1 }
}
function Write-Log([string]$msg) {
  $line = "[{0}] {1}" -f (Get-Date).ToString("s"), $msg
  Add-Content -Path $log -Value $line -Encoding UTF8
  Write-Host $line
}


function Get-OwnedServer {
  $serverPath = [IO.Path]::GetFullPath($server)
  $escaped = [regex]::Escape($serverPath)
  @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $_.Name -match '^python(w|3(\.\d+)?)?\.exe$' -and
    $_.CommandLine -match ('(?i)(?:^|\s)"?' + $escaped + '"?(?=\s|$)')
  })
}
function Get-BridgeToken {
  if ($env:NEXUS_BRIDGE_TOKEN) { return $env:NEXUS_BRIDGE_TOKEN.Trim() }
  foreach ($tokenPath in @((Join-Path $Root "workspace\bridge\.token"), (Join-Path $Root "workspace\certs\.bridge_token"))) {
    if (Test-Path -LiteralPath $tokenPath) {
      $token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
      if ($token) { return $token }
    }
  }
  return ""
}
function Test-NexusHealth {
  param([System.Diagnostics.Stopwatch]$ReadinessTimer = [System.Diagnostics.Stopwatch]::StartNew())
  function Get-HealthTimeout([int]$Limit) {
    $remaining = [int][Math]::Floor($readinessBudgetSec - $ReadinessTimer.Elapsed.TotalSeconds)
    if ($remaining -lt 1) { throw "NEXUS_START_READINESS_TIMEOUT" }
    return [Math]::Min($Limit, $remaining)
  }
  try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec (Get-HealthTimeout $coreHealthTimeoutSec)
    if ($r.ok -ne $true) { return $false }
    $token = Get-BridgeToken
    if (-not $token) { return $false }
    $headers = @{ Authorization = "Bearer $token" }
    $bridge = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/bridge/health" -Headers $headers -TimeoutSec (Get-HealthTimeout 3)
    if ($bridge.success -ne $true -or $bridge.ok -ne $true -or
        $bridge.protocol -ne "NEXUS_AGENT_BRIDGE_PROTOCOL" -or $bridge.marker -ne "NEXUS_AGENT_BRIDGE_V1") { return $false }
    $caps = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/capabilities" -Headers $headers -TimeoutSec (Get-HealthTimeout 3)
    if ($caps.success -ne $true -or $caps.protocol -ne "NEXUS_AGENT_BRIDGE_PROTOCOL" -or
        $caps.schema -ne "nexus.bridge.capabilities.v1") { return $false }
    $ping = @($caps.tasks | Where-Object { $_.name -eq "ping" -and $_.permission -eq "READ_ONLY" })
    if ($ping.Count -ne 1) { return $false }
    $pingBody = '{"task":"ping","params":{"echo":"nexus-start-health"}}'
    $probe = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/task" -Method Post -Headers $headers -ContentType "application/json" -Body $pingBody -TimeoutSec (Get-HealthTimeout 3)
    return ($probe.success -eq $true -and $probe.task.task -eq "ping" -and
            $probe.task.status -eq "succeeded" -and $probe.task.permission -eq "READ_ONLY" -and
            $probe.task.result.pong -eq $true -and $probe.task.result.echo -eq "nexus-start-health")
  } catch { return $false }
}

Write-Log "start_nexus begin root=$Root port=$Port"

# --- Start-Lock: verhindert 2x paralleles start_nexus ---
$lockStream = $null
$gotLock = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $lockStream = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    $gotLock = $true
    break
  } catch {
    Start-Sleep -Milliseconds 250
  }
}
if (-not $gotLock) {
  Write-Log "ERROR: start lock busy - anderer start_nexus laeuft noch"
  Write-Host "NEXUS start bereits aktiv (Lock). Abbruch." -ForegroundColor Yellow
  exit 2
}
try {
  $bytes = [Text.Encoding]::ASCII.GetBytes(("pid={0} ts={1}" -f $PID, (Get-Date).ToString("o")))
  $lockStream.SetLength(0)
  $lockStream.Write($bytes, 0, $bytes.Length)
  $lockStream.Flush()

  # Python zentral (02.11.2026): config\python_path.txt via Get-NexusPython, KEIN Fallback auf py/python/WindowsApps
  try {
    . (Join-Path $Root "nexus_python.ps1")
    $py = Get-NexusPython -Root $Root
  } catch {
    Write-Log ("ERROR python: {0}" -f $_.Exception.Message)
    Write-Host ("NEXUS Start abgebrochen: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 3
  }
  Write-Log "python=$py"

  # Load Bridge auth before either reusing a server or starting a new one.
  $env:NEXUS_BRIDGE_TOKEN = Get-BridgeToken
  if ($env:NEXUS_BRIDGE_TOKEN) {
    Write-Log "bridge_token available (value withheld)"
  } else {
    Write-Log "bridge_token: none yet - server will generate workspace/bridge/.token on boot"
  }

  $owned = @(Get-OwnedServer)
  $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
  if ($owned.Count -gt 1) {
    Write-Log "BLOCKED: multiple project servers exist; no process terminated"
    exit 12
  }
  if ($listeners.Count -gt 0 -or $owned.Count -gt 0) {
    if ($owned.Count -eq 1 -and $listeners.Count -eq 1 -and
        [int]$listeners[0] -eq [int]$owned[0].ProcessId -and (Test-NexusHealth)) {
      Write-Log ("REUSED existing NEXUS pid={0}; active work preserved" -f $owned[0].ProcessId)
      exit 0
    }
    Write-Log "BLOCKED: existing process/port not verified healthy; no restart attempted"
    exit 13
  }

  $env:NEXUS_PORT = "$Port"
  $env:NEXUS_HOST = $HostAddr
  # Bearer for /api/assistant: file -> Env (if not already set)
  if (-not $env:NEXUS_ASSISTANT_TOKEN) {
    $tokFile = Join-Path $Root "workspace\apex\assistant_token.txt"
    if (Test-Path $tokFile) {
      $env:NEXUS_ASSISTANT_TOKEN = (Get-Content $tokFile -Raw).Trim()
      $t = $env:NEXUS_ASSISTANT_TOKEN
      $mask = if ($t -and $t.Length -ge 4) { "..." + $t.Substring($t.Length - 4) } else { "(empty)" }
      Write-Log "assistant_token loaded from file (value withheld)"
    } else {
      Write-Log "WARN: no assistant_token.txt - /api/assistant returns 401 until setup_assistant_token.ps1"
    }
  } else {
    Write-Log "assistant_token from Env NEXUS_ASSISTANT_TOKEN"
  }

  # OpenAI cloud orchestrator key: NUR bei NEXUS_ALLOW_PAID=1 (Kostenregel: keine Bezahl-APIs als Default)
  if ($env:NEXUS_ALLOW_PAID -ne '1') {
    if ($env:OPENAI_API_KEY) { Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue }
    Write-Log "openai_api_key: skipped (NEXUS_ALLOW_PAID!=1, local-only)"
  } elseif (-not $env:OPENAI_API_KEY) {
    $oa = Join-Path $env:LOCALAPPDATA "NEXUS\openai.env"
    if (Test-Path $oa) {
      Get-Content $oa | ForEach-Object {
        if ($_ -match '^\s*OPENAI_API_KEY\s*=\s*(.+)$') {
          $env:OPENAI_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
        }
      }
      if ($env:OPENAI_API_KEY) {
        $m = $env:OPENAI_API_KEY
        $mask = if ($m.Length -ge 4) { "..." + $m.Substring($m.Length - 4) } else { "(set)" }
        Write-Log "openai_api_key loaded from AppData (value withheld)"
      }
    } else {
      Write-Log "openai_api_key: AppData openai.env missing (cloud orchestrate needs key)"
    }
  } else {
    Write-Log "openai_api_key from Env OPENAI_API_KEY"
  }
  # Ungepufferte Ausgabe, damit nexus_server.out/err.log live befuellt werden
  $env:PYTHONUNBUFFERED = '1'
  # WICHTIG: stdout und stderr NIEMALS auf dieselbe Datei
  $serverOut = Join-Path $logDir "nexus_server.out.log"
  $serverErr = Join-Path $logDir "nexus_server.err.log"
  Write-Log "start: $py `"$server`" out=$serverOut err=$serverErr"
  $argList = if ($py -match '(?i)py\.exe$') { @('-3','-B','-u', $server) } else { @('-B','-u', $server) }
  Write-Log ("std_handle_inheritance_cleared={0}" -f (Disable-NexusStdHandleInheritance))
  # Pipefix 16.11.2026 (Nexus-Tools): KEIN Start-Process -Redirect* mehr - vererbt die Pipe des Aufrufers trotz
  # Disable-NexusStdHandleInheritance (Trockentest belegt). ShellExecute (Start-Process ohne Umleitung) auf cmd.exe,
  # cmd leitet in Logdateien auf G: um. Env und Arbeitsverzeichnis ($Root) erbt das Kind.
  $cmdArgs = ($argList | ForEach-Object { '"{0}"' -f $_ }) -join ' '
  $launcher = Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /c ""{0}" {1} 1>"{2}" 2>"{3}""' -f $py, $cmdArgs, $serverOut, $serverErr) -WorkingDirectory $Root -WindowStyle Hidden -PassThru
  Write-Log ("launcher=cmd pid={0}" -f $launcher.Id)
  # Server-PID frueh als Kind des Launchers (fuer pidFile/Prioritaet), nach Readiness Abgleich mit dem Listener auf $Port
  $serverPid = 0
  $pidDeadline = (Get-Date).AddSeconds(10)
  while ($serverPid -eq 0 -and (Get-Date) -lt $pidDeadline) {
    $kid = @(Get-OwnedServer | Where-Object { [int]$_.ParentProcessId -eq [int]$launcher.Id } | Select-Object -First 1)
    if ($kid.Count -gt 0) { $serverPid = [int]$kid[0].ProcessId } else { Start-Sleep -Milliseconds 300 }
  }
  function Set-ServerPid([int]$id) {
    Set-Content -Path $pidFile -Value $id -Encoding ASCII
    try { (Get-Process -Id $id).PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal; Write-Log "priority=BelowNormal" } catch { Write-Log ("priority set failed: {0}" -f $_.Exception.Message) }
    Write-Log ("started pid={0}" -f $id)
  }
  if ($serverPid -gt 0) { Set-ServerPid $serverPid } else { Write-Log "WARN server pid not found as launcher child; using port listener after readiness" }

  $ok = $false
  $readinessTimer = [System.Diagnostics.Stopwatch]::StartNew()
  while ($readinessTimer.Elapsed.TotalSeconds -lt $readinessBudgetSec) {
    if (Test-NexusHealth -ReadinessTimer $readinessTimer) { $ok = $true; Write-Log "core and authenticated bridge health/capabilities/task verified"; break }
    if ($readinessTimer.Elapsed.TotalSeconds -ge ($readinessBudgetSec - 1)) { break }
    Start-Sleep -Milliseconds 600
  }

  $owned = @(Get-OwnedServer)
  $listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($listener.Count -gt 0) {
    $listenerPid = [int]$listener[0].OwningProcess
    Write-Log ("listener port={0} pid={1}" -f $Port, $listenerPid)
    if ($serverPid -eq 0) { $serverPid = $listenerPid; Set-ServerPid $serverPid }
    elseif ($listenerPid -ne $serverPid) { Write-Log ("WARN listener pid={0} differs from launcher child pid={1}" -f $listenerPid, $serverPid) }
  }
  if ($owned.Count -ne 1 -or [int]$owned[0].ProcessId -ne [int]$serverPid) {
    Write-Log "BLOCKED: process identity/single-instance verification failed; no process terminated"
    exit 14
  }

  if ($ok) {
    Write-Host ""
    Write-Host "NEXUS ONLINE  http://127.0.0.1:$Port/" -ForegroundColor Cyan
    Write-Host "Mobile        http://127.0.0.1:$Port/m"
    Write-Host "Mobile HTTPS  https://127.0.0.1:8443/m"
    Write-Host "LAN Handy     https://<DEINE-PC-IP>:8443/m  (Anruf/Mic)  http://<IP>:$Port/m (Chat)"
    Write-Host "Firewall:     .\enable_lan_firewall.ps1  (TCP 8299+8443)"
    exit 0
  } else {
    Write-Log "WARN: Core/Bridge health timeout - check $serverOut / $serverErr"
    exit 1
  }
}
catch {
  Write-Log ("ERROR startup failed: {0}" -f $_.Exception.GetType().Name)
  exit 1
}
finally {
  try { if ($lockStream) { $lockStream.Close() } } catch {}
  # Keep lock inode stable; only release the exclusive handle.
}
