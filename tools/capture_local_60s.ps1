param(
    [string]$ImuPort = 'COM12',
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $repoRoot 'web'
$baseUrl = "http://127.0.0.1:$Port"

function Get-CaptureStatus {
    $result = Invoke-RestMethod -Uri "$baseUrl/api/local-capture/status" -TimeoutSec 3
    if (-not $result.PSObject.Properties['state'] -or -not $result.PSObject.Properties['sessions']) {
        throw '此端口上的服务不是本机采集服务。'
    }
    return $result
}

try {
    Write-Host 'NA70 + IMU 一键录制：60 秒' -ForegroundColor Cyan
    Write-Host "IMU：$ImuPort / 460800 波特；雷达：UDP 2368"
    $status = $null
    try { $status = Get-CaptureStatus } catch {}
    if ($null -eq $status) {
        $probe = New-Object System.Net.Sockets.TcpClient
        try {
            $probe.Connect('127.0.0.1', $Port)
            throw "端口 $Port 已被占用，但本机采集服务不可用。"
        } catch [System.Net.Sockets.SocketException] {
            # Nothing is listening; start the existing backend below.
        } finally {
            $probe.Close()
        }
        $python = Join-Path $webRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) {
            throw 'Web 运行环境不存在，请先运行一次启动WebUI.bat 安装环境。'
        }
        Start-Process -FilePath $python -ArgumentList @('-m', 'backend.run', '--port', "$Port", '--no-browser') -WorkingDirectory $webRoot -WindowStyle Hidden
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 1
            try { $status = Get-CaptureStatus; break } catch {}
        }
        if ($null -eq $status) { throw '本机采集服务未能启动，请检查 WebUI 安装。' }
    }

    $body = @{ task = 'MANUAL-60S'; duration_s = 60; imu_port = $ImuPort; imu_baud = 460800 } | ConvertTo-Json -Compress
    $started = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/local-capture/start" -ContentType 'application/json' -Body $body -TimeoutSec 5
    $sessionId = $started.session_id
    if (-not $sessionId) { throw '采集服务没有返回会话编号。' }
    Write-Host "已开始，会话：$sessionId"
    $deadline = (Get-Date).AddSeconds(150)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        $status = Get-CaptureStatus
        if ($status.session_id -and $status.session_id -ne $sessionId) {
            throw "采集会话发生变化，请在 WebUI 中检查 $sessionId。"
        }
        if ($status.state -eq 'failed') {
            throw "采集失败：$($status.error)。会话目录：$($status.session_path)"
        }
        if ($status.state -eq 'idle') {
            if (-not $status.last -or $status.last.session_id -ne $sessionId) {
                throw "未找到会话 $sessionId 的最终结果，请检查 WebUI。"
            }
            Write-Host "录制结束，状态：$($status.last.status)" -ForegroundColor Cyan
            Write-Host "保存位置：$($status.last.path)"
            foreach ($check in $status.last.failed_checks) {
                Write-Host "  $($check.name): $($check.detail)" -ForegroundColor Yellow
            }
            if ($status.last.status -eq 'failed') { throw '采集未收到完整的双路数据，请检查设备连接。' }
            if ($status.last.status -eq 'degraded') {
                Write-Host '本机 UDP 内核丢包数可能无法获取；degraded 不代表已确认丢包。' -ForegroundColor Yellow
            }
            exit 0
        }
        if ($status.state -eq 'recording') {
            Write-Host ("剩余 {0,4:N0} 秒  IMU：{1} 帧  雷达：{2} 包" -f $status.remaining_s, $status.devices.imu.frames, $status.devices.lidar.captured)
        } else {
            Write-Host "状态：$($status.state)"
        }
    }
    throw "等待会话 $sessionId 超时；采集可能仍在继续，请检查 WebUI。"
} catch {
    Write-Host "错误：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
