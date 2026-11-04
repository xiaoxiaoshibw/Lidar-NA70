param(
    [string]$BaseUrl = "http://127.0.0.1:8791",
    [int]$Duration = 80,
    [string]$JetsonHost = "jetson@192.168.31.168",
    [string]$SessionRoot = "/home/jetson/na70-sessions",
    [string]$LocalRoot = "D:\Code\radar\radar_capture_data\na70_sessions"
)
$ErrorActionPreference = "Stop"
Write-Host "=== NA70 extrinsic rotation capture ===" -ForegroundColor Cyan
Write-Host "Place the rig on a stable support, optical window facing one large flat wall."
Write-Host "Wall distance: at least 8 m, prefer 9-15 m. This sensor returns nothing closer" -ForegroundColor Yellow
Write-Host "than about 7.5 m, so a 2-5 m wall yields zero points." -ForegroundColor Yellow
Write-Host "Timeline after GO:"
Write-Host "  0-10 s   still"
Write-Host " 10-25 s   yaw left/right about 30 deg"
Write-Host " 25-40 s   pitch up/down about 20 deg"
Write-Host " 40-55 s   roll left/right about 20 deg"
Write-Host " 55-70 s   slow combined 3-axis rotation"
Write-Host " 70-80 s   still"
Write-Host ""
for ($i = 5; $i -ge 1; $i--) {
    Write-Host "  starting in $i s ..." -ForegroundColor Yellow
    Start-Sleep -Seconds 1
}
$body = @{
    task = "CAL-ROT"
    duration_s = $Duration
    operator = @{ orientation = "optical window forward, top up"; note = "extrinsic slow 3-axis rotation" }
    note = "CAL-ROT: slow 3-axis rotation for extrinsic calibration; one large flat wall"
} | ConvertTo-Json -Depth 3
$start = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/session/start" -ContentType "application/json" -Body $body
$sessionId = $start.session_id
Write-Host "GO  session=$sessionId  duration=${Duration}s" -ForegroundColor Green
$started = Get-Date
while ($true) {
    Start-Sleep -Seconds 2
    $status = Invoke-RestMethod -Uri "$BaseUrl/api/status"
    $elapsed = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    Write-Host ("  {0,5:N1}s  state={1}  remaining={2:N1}s" -f $elapsed, $status.state, $status.remaining_s)
    if ($status.state -eq "idle") { break }
    if ($status.state -eq "failed") {
        throw ("session {0} FAILED: {1}. Do not retry blindly; keep the session on the Jetson and tell the AI." -f $status.session_id, $status.error)
    }
}
Write-Host "Capture finished; copying the session to local ..." -ForegroundColor Cyan
$remote = "$JetsonHost`:$SessionRoot/$sessionId"
$target = Join-Path $LocalRoot $sessionId
if (Test-Path $target) { throw "local target already exists: $target" }
& scp -r -o BatchMode=yes $remote $LocalRoot
if ($LASTEXITCODE -ne 0) { throw "scp failed; session remains on Jetson: $remote" }
Write-Host "Copied to $target" -ForegroundColor Green
Write-Host "Next: run the local plane extrinsic analysis (no WSL)."
