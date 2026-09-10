param(
    [string]$Pcap = "",
    [double]$Rate = 1000,
    [string]$Distro = "Ubuntu-22.04"
)

$ErrorActionPreference = "Stop"
$repoPath = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Pcap)) {
    $Pcap = Join-Path $repoPath "radar_capture_data\2.pcap"
}
$pcapPath = (Resolve-Path -LiteralPath $Pcap).Path
$bashScriptPath = Join-Path $PSScriptRoot "wsl2_pcap_rviz.sh"

function ConvertTo-WslPath([string]$WindowsPath) {
    $fullPath = [System.IO.Path]::GetFullPath($WindowsPath)
    if ($fullPath -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "只支持本机盘符路径：$fullPath"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace('\', '/')
    return "/mnt/$drive/$tail"
}

$wslPcap = ConvertTo-WslPath $pcapPath
$wslScript = ConvertTo-WslPath $bashScriptPath

# wsl.exe 可以直接以发行版 root 身份执行；该设置仅持续到 WSL 实例关闭。
& wsl.exe -d $Distro -u root -- sysctl -q -w `
    net.core.rmem_max=16777216 net.core.wmem_max=16777216
if ($LASTEXITCODE -ne 0) {
    throw "无法提高 WSL socket 缓冲。"
}

& wsl.exe -d $Distro -- bash $wslScript $wslPcap $Rate
exit $LASTEXITCODE
