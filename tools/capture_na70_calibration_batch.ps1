param(
    [string]$BaseUrl = "http://127.0.0.1:8791",
    [string]$JetsonHost = "jetson@192.168.31.168",
    [string]$SessionRoot = "/home/jetson/na70-sessions",
    [string]$LocalRoot = "D:\Code\radar\radar_capture_data\na70_sessions",
    [int]$StaticSeconds = 120,
    [int]$RotationSeconds = 80,
    [int]$FaceSeconds = 25
)
$ErrorActionPreference = "Stop"

function Wait-Idle {
    while ($true) {
        Start-Sleep -Seconds 2
        $status = Invoke-RestMethod -Uri "$BaseUrl/api/status"
        if ($status.state -eq "idle") { return $status }
        if ($status.state -eq "failed") {
            throw ("session {0} FAILED: {1}. Do not retry blindly; keep the session on the Jetson and tell the AI." -f $status.session_id, $status.error)
        }
        Write-Host ("    state={0} remaining={1:N1}s" -f $status.state, $status.remaining_s)
    }
}

function Start-Session([string]$task, [int]$duration, [string]$note) {
    $body = @{
        task = $task
        duration_s = $duration
        operator = @{ note = $note }
        note = $note
    } | ConvertTo-Json -Depth 3
    $start = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/session/start" `
        -ContentType "application/json" -Body $body
    Write-Host ("  recording {0}  session={1}  {2}s" -f $task, $start.session_id, $duration) -ForegroundColor Green
    Wait-Idle | Out-Null
    return [string]$start.session_id
}

function Copy-Session([string]$sessionId) {
    $target = Join-Path $LocalRoot $sessionId
    if (Test-Path $target) { throw "local target already exists: $target" }
    $remote = "${JetsonHost}:$SessionRoot/$sessionId"
    & scp -r -o BatchMode=yes $remote $LocalRoot
    if ($LASTEXITCODE -ne 0) { throw "scp failed; session remains on Jetson: $remote" }
    Write-Host "  copied $sessionId" -ForegroundColor Green
    return $target
}

Write-Host "=== NA70 full calibration batch ===" -ForegroundColor Cyan
Write-Host "Sessions: STATIC ${StaticSeconds}s, ROT ${RotationSeconds}s, six FACES ${FaceSeconds}s each."
Write-Host "All data stays on Nano during recording; it is copied to this PC at the end."
Write-Host ""

Read-Host "STATIC: keep the whole rig still, then press Enter"
$staticId = Start-Session "CAL-STATIC" $StaticSeconds "still rig; IMU baseline"

Write-Host ""
Write-Host "ROT: place the rig facing ONE large flat wall, at least 8 m away (prefer 9-15 m), no glass/corner." -ForegroundColor Yellow
Write-Host "     Closer than ~7.5 m gives zero returns on this sensor." -ForegroundColor Yellow
Write-Host "After GO: 0-10s still; 10-25s yaw +/-30; 25-40s pitch +/-20; 40-55s roll +/-20; 55-70s combined; 70-80s still."
Read-Host "Press Enter when ready"
$rotationId = Start-Session "CAL-ROT" $RotationSeconds "slow 3-axis rotation; one large flat wall"

$faces = @("+z", "-z", "+x", "-x", "+y", "-y")
$faceSessions = @{}
foreach ($face in $faces) {
    Write-Host ""
    Write-Host "FACE ${face}: place the rig with $face pointing up, keep it still." -ForegroundColor Yellow
    Read-Host "Press Enter when placed"
    $id = Start-Session "CAL-FACE-$face" $FaceSeconds "face $face up; still"
    $faceSessions[$face] = $id
}

Write-Host ""
Write-Host "Copying all sessions to local ..." -ForegroundColor Cyan
$manifest = @{
    schema = "na70-calibration-batch-v1"
    created_local = (Get-Date).ToString("s")
    static_seconds = $StaticSeconds
    rotation_seconds = $RotationSeconds
    face_seconds = $FaceSeconds
    sessions = @{
        STATIC = @{ task = "CAL-STATIC"; session_id = $staticId; local = (Copy-Session $staticId) }
        ROT = @{ task = "CAL-ROT"; session_id = $rotationId; local = (Copy-Session $rotationId) }
        FACES = @{}
    }
}
foreach ($face in $faces) {
    $id = $faceSessions[$face]
    $manifest.sessions.FACES[$face] = @{
        task = "CAL-FACE-$face"
        session_id = $id
        local = (Copy-Session $id)
    }
}
$batchPath = Join-Path $LocalRoot ("calibration-batch-" + (Get-Date).ToString("yyyyMMdd-HHmmss") + ".json")
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $batchPath
Write-Host "Batch manifest: $batchPath" -ForegroundColor Green
Write-Host "Tell the AI: calibration batch is ready."
