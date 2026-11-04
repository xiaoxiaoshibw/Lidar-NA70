param(
    [string[]]$JetsonHosts = @('jetson@192.168.31.168', 'jetson@192.168.3.129'),
    [ValidateRange(1024, 65535)][int]$LocalPort = 8791,
    [ValidateRange(1024, 65535)][int]$RemotePort = 8792,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$address = "http://127.0.0.1:$LocalPort/api/status"
try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $LocalPort)
    $listener.Start()
} catch {
    throw "本机端口 $LocalPort 已被占用，请检查已有采集连接；本工具不会关闭其他进程。"
} finally {
    if ($listener) { $listener.Stop() }
}
foreach ($target in $JetsonHosts) {
    if ($target -notmatch '^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+$') { throw 'Jetson 地址格式无效。' }
    $arguments = @('-N', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o',
        'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
        '-L', "127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}", $target)
    $process = Start-Process -FilePath 'ssh.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
    $connected = $false
    try {
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Milliseconds 200
            $process.Refresh()
            if ($process.HasExited) { break }
            try {
                $status = Invoke-RestMethod -TimeoutSec 1 -Uri $address
                if ($status.state -in @('idle', 'starting', 'recording', 'stopping', 'failed')) {
                    $connected = $true
                    break
                }
            } catch { }
        }
        if ($connected) {
            Write-Output "已连接 $target，采集连接进程 PID $($process.Id)。"
            if (!$NoBrowser) { Start-Process 'http://127.0.0.1:8765/capture/' }
            return
        }
    } finally {
        if (!$connected -and !$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
    }
}
throw '无法连接 Jetson 采集服务。请检查 Jetson、网络和采集服务；原有会话未被更改。'
