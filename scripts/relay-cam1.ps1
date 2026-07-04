<#
.SYNOPSIS
  Relay Godrej CAM 1 (LAN) into MediaMTX on EC2 with preflight checks and auto-restart.

.DESCRIPTION
  Runs preflight network checks, probes the camera's RTSP source, then enters a loop
  that pumps the camera's HEVC sub-stream into MediaMTX over RTSP. ffmpeg restarts
  automatically on crash. All output is mirrored to a timestamped log file.

  Press Ctrl+C to stop cleanly. The active ffmpeg child process is killed via the
  PowerShell finally{} block.

.PARAMETER Source
  Source RTSP URL on the LAN (camera credentials included by the camera's URL
  scheme). Defaults to the ARGUS_RELAY_SOURCE environment variable so no
  camera credentials live in this script or in shell history.

.PARAMETER Dest
  Destination RTSP URL on EC2 MediaMTX. Defaults to the ARGUS_RELAY_DEST
  environment variable. MediaMTX requires publish credentials, so the URL
  must be in the form:
    rtsp://mtx_publisher:<password>@<ec2-host>:8554/live/cam1

.PARAMETER LogDir
  Directory for timestamped log file. Default = .\logs

.PARAMETER MaxRestarts
  Stop after this many consecutive ffmpeg crashes. Default = 0 (unlimited).

.EXAMPLE
  .\scripts\relay-cam1.ps1
  # Use defaults: CAM 1 sub-stream -> EC2 MediaMTX, infinite auto-restart.

.EXAMPLE
  .\scripts\relay-cam1.ps1 -Source "rtsp://192.168.29.10:554/user=admin&password=&channel=1&stream=0.sdp"
  # Use main stream (1080p) instead of sub-stream.
#>

[CmdletBinding()]
param(
  [string] $Source = $env:ARGUS_RELAY_SOURCE,
  [string] $Dest   = $env:ARGUS_RELAY_DEST,
  [string] $LogDir = (Join-Path $PSScriptRoot "..\logs"),
  [int]    $MaxRestarts = 0
)

$ErrorActionPreference = "Stop"

if (-not $Source) {
  Write-Host "ERROR: No source URL. Set ARGUS_RELAY_SOURCE or pass -Source." -ForegroundColor Red
  Write-Host '  e.g. $env:ARGUS_RELAY_SOURCE = "rtsp://<cam-lan-ip>:554/user=admin&password=<pw>&channel=1&stream=1.sdp"'
  exit 1
}
if (-not $Dest) {
  Write-Host "ERROR: No destination URL. Set ARGUS_RELAY_DEST or pass -Dest." -ForegroundColor Red
  Write-Host '  e.g. $env:ARGUS_RELAY_DEST = "rtsp://mtx_publisher:<pw>@<ec2-host>:8554/live/cam1"'
  exit 1
}

function Write-Log {
  param([string] $Level, [string] $Message)
  $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  $line = "[$ts] [$Level] $Message"
  Write-Host $line
  if ($script:LogPath) { Add-Content -Path $script:LogPath -Value $line -Encoding utf8 }
}

function Hide-UrlSecrets {
  param([string] $Url)
  # Mask rtsp://user:pass@host and &password=... query credentials in logs.
  $masked = $Url -replace '(?<=rtsp://)[^/@]+:[^/@]+(?=@)', '***:***'
  return ($masked -replace '(?i)(password=)[^&\s]*', '$1***')
}

function Test-RtspSource {
  param([string] $Url)
  $out = & ffprobe -rtsp_transport tcp -timeout 4000000 -v error `
                   -show_entries stream=codec_name,width,height,r_frame_rate `
                   -of default=nw=1 $Url 2>&1
  if ($LASTEXITCODE -eq 0 -and ($out -join "`n") -match "codec_name") {
    return ($out -join " | ")
  }
  return $null
}

# --- Setup log file -----------------------------------------------------------
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
$script:LogPath = Join-Path $LogDir ("relay-cam1_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
Write-Log "INFO" "Log file: $script:LogPath"
Write-Log "INFO" "Source : $(Hide-UrlSecrets $Source)"
Write-Log "INFO" "Dest   : $(Hide-UrlSecrets $Dest)"

# --- Preflight 1: tools -------------------------------------------------------
foreach ($tool in @("ffmpeg", "ffprobe")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    Write-Log "ERROR" "$tool not found on PATH. Install with: winget install Gyan.FFmpeg"
    exit 1
  }
}

# --- Preflight 2: parse hosts from URLs --------------------------------------
$camHost = ([Uri]$Source).Host
$ec2Host = ([Uri]$Dest).Host
$ec2Port = ([Uri]$Dest).Port

# --- Preflight 3: LAN reachability to camera ---------------------------------
Write-Log "INFO" "Pinging camera $camHost ..."
if (-not (Test-Connection -ComputerName $camHost -Count 2 -Quiet -ErrorAction SilentlyContinue)) {
  Write-Log "ERROR" "Camera $camHost is unreachable. Check this machine is on the camera LAN (run 'ipconfig')."
  exit 2
}
Write-Log "OK" "Camera $camHost reachable on LAN."

# --- Preflight 4: EC2 reachability -------------------------------------------
Write-Log "INFO" "Probing EC2 $ec2Host`:$ec2Port ..."
$tcp = Test-NetConnection -ComputerName $ec2Host -Port $ec2Port -WarningAction SilentlyContinue
if (-not $tcp.TcpTestSucceeded) {
  Write-Log "ERROR" "Cannot connect to EC2 MediaMTX at $ec2Host`:$ec2Port. Check security group + EC2 status."
  exit 3
}
Write-Log "OK" "EC2 $ec2Host`:$ec2Port reachable."

# --- Preflight 5: source RTSP probe ------------------------------------------
Write-Log "INFO" "Probing source RTSP stream ..."
$streamInfo = Test-RtspSource -Url $Source
if (-not $streamInfo) {
  Write-Log "ERROR" "Source RTSP probe failed. URL may be wrong or camera busy. Try VLC manually."
  exit 4
}
Write-Log "OK" "Source stream: $streamInfo"

# --- Main loop: ffmpeg with auto-restart -------------------------------------
$restartCount = 0
$ffmpegArgs = @(
  "-hide_banner",
  "-loglevel", "warning",
  "-rtsp_transport", "tcp",
  "-i", $Source,
  "-c", "copy",
  "-f", "rtsp",
  "-rtsp_transport", "tcp",
  $Dest
)

$child = $null
try {
  while ($true) {
    Write-Log "INFO" "Starting ffmpeg (run #$($restartCount + 1)) ..."
    $child = Start-Process -FilePath ffmpeg `
                           -ArgumentList $ffmpegArgs `
                           -PassThru `
                           -NoNewWindow `
                           -RedirectStandardError $script:LogPath `
                           -ErrorAction Stop
    $startedAt = Get-Date
    $child.WaitForExit()
    $ranSec = [int]((Get-Date) - $startedAt).TotalSeconds
    Write-Log "WARN" "ffmpeg exited (code=$($child.ExitCode), ran=$ranSec s)."

    $restartCount++
    if ($MaxRestarts -gt 0 -and $restartCount -ge $MaxRestarts) {
      Write-Log "ERROR" "Hit MaxRestarts=$MaxRestarts. Stopping."
      break
    }
    # If ffmpeg dies in <5s repeatedly it's a hard failure (wrong URL, etc) — back off.
    $backoff = if ($ranSec -lt 5) { 10 } else { 3 }
    Write-Log "INFO" "Restarting in ${backoff}s ..."
    Start-Sleep -Seconds $backoff
  }
}
finally {
  if ($child -and -not $child.HasExited) {
    Write-Log "INFO" "Ctrl+C received. Killing ffmpeg pid=$($child.Id)..."
    try { $child.Kill() } catch { }
  }
  Write-Log "INFO" "Relay shutdown complete."
}
