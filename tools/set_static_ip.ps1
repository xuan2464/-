#Requires -Version 5.1
<#
  把有线网卡设成静态 IP（连机器狗用），或恢复自动获取。
  这个脚本需要管理员权限，由 连接机器狗.bat / connect.ps1 自动提权调用，
  也可以右键"以管理员身份运行"手动执行：

    powershell -ExecutionPolicy Bypass -File tools\set_static_ip.ps1 -Enable
    powershell -ExecutionPolicy Bypass -File tools\set_static_ip.ps1 -Disable
#>
[CmdletBinding()]
param(
  [switch]$Enable,
  [switch]$Disable,
  [string]$Address = '192.168.1.50',
  [int]$Prefix = 24,
  [int]$Index = 0,
  [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

function Say([string]$m) { if (-not $Quiet) { Write-Host "  $m" } }

# 候选：有线网卡（排除虚拟网卡、WiFi/蓝牙）；已连接的优先
function Get-WiredNic {
  Get-NetAdapter -ErrorAction SilentlyContinue |
    Where-Object {
      $_.InterfaceDescription -notmatch 'VirtualBox|VMware|Hyper-V|Loopback|TAP|Wi-Fi Direct|Bluetooth' -and
      $_.InterfaceDescription -notmatch 'WiFi|Wi-Fi|802\.11|Wireless|WLAN'
    } |
    Sort-Object -Property `
      @{ Expression = { if ($_.Status -eq 'Up') { 0 } else { 1 } } }, `
      @{ Expression = { if ($_.InterfaceDescription -match 'Ethernet|GbE|2\.5G|Controller|Intel|Realtek Gaming') { 0 } else { 1 } } }
}

if (-not $Enable -and -not $Disable) { $Enable = $true }

if ($Index -gt 0) {
  $nic = Get-NetAdapter -InterfaceIndex $Index -ErrorAction Stop
} else {
  $nic = Get-WiredNic | Select-Object -First 1
}

if (-not $nic) {
  if (-not $Quiet) { Write-Host '  [错误] 没有找到有线网卡（这台机器只有 WiFi？）' -ForegroundColor Red }
  exit 1
}

Say "网卡：$($nic.Name)  ($($nic.InterfaceDescription))"
if ($nic.Status -ne 'Up') {
  Say "[warn] 这块网卡现在是「已断开」——网线没插好，或者机器狗还没通电"
  Say '       IP 会先设好，等网线插上、机器狗开机就自动生效'
}

if ($Disable) {
  Set-NetIPInterface -InterfaceIndex $nic.ifIndex -Dhcp Enabled
  Remove-NetIPAddress -InterfaceIndex $nic.ifIndex -Confirm:$false -ErrorAction SilentlyContinue
  Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.PrefixOrigin -eq 'Manual' } |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
  ipconfig /renew "$($nic.Name)" | Out-Null
  Say '[ok] 已恢复自动获取 IP'
  exit 0
}

# 已有的静态地址先清掉，避免一个网卡挂多个地址
Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.PrefixOrigin -ne 'Manual' } |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue

try { Set-NetIPInterface -InterfaceIndex $nic.ifIndex -Dhcp Disabled -ErrorAction Stop } catch { }

if (-not (Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq $Address })) {
  New-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $Address -PrefixLength $Prefix -ErrorAction Stop | Out-Null
}

Say "[ok] 已设为 $Address/$Prefix"

# 顺手给一份网关/ARP 环境，方便立刻 ping 通（不影响无网关的场景）
$ok = Test-Connection -ComputerName '192.168.1.103' -Count 2 -Quiet -ErrorAction SilentlyContinue
if ($ok) { Say '[ok] 已经能 ping 通 192.168.1.103' } else { Say '[warn] 现在 ping 不通 192.168.1.103（机器狗可能还没开机完）' }
exit 0
