#Requires -Version 5.1
<#
  绝影 Lite3 · 相机画面 —— 一键连接机器狗并部署

  用法（在项目根目录双击 连接机器狗.bat 即可，也可直接跑本脚本）：
    .\tools\connect.ps1                    # 连机器狗 → 部署 → 启动 → 打开浏览器（自动挑信号源）
    .\tools\connect.ps1 -Source ros        # 不想停 realsense 服务，改从 ROS 话题取图
    .\tools\connect.ps1 -Source demo       # 只验证网页，不碰相机
    .\tools\connect.ps1 -Check             # 只看当前能不能连上、服务在不在
    .\tools\connect.ps1 -Stop              # 停掉机器狗上的服务
    .\tools\connect.ps1 -Wifi "Lite3-1234" # 先切到机器狗 WiFi（已保存过的热点）

  说明：全程只需要你自己输 2~3 次密码，密码都是一个英文单引号 '
#>
[CmdletBinding()]
param(
  [string]$RobotIp = '192.168.1.103',
  [string]$User = 'ysc',
  [int]$Port = 8080,
  [ValidateSet('auto', 'realsense', 'ros', 'demo')]
  [string]$Source = 'auto',
  [string]$RemoteDir = '/home/ysc/camera_web',
  [string]$Wifi = '',
  [int]$WaitSeconds = 180,
  [switch]$Check,
  [switch]$Stop,
  [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

if ($Check -and $WaitSeconds -gt 5) { $WaitSeconds = 0 }   # 只检查时不干等

$Root = Split-Path -Parent $PSScriptRoot
$Target = "$User@$RobotIp"
$Url = "http://${RobotIp}:$Port"

# ------------------------------------------------------------------ 输出
function Line { Write-Host ('  ' + ('-' * 58)) -ForegroundColor DarkGray }
function Title([string]$t) {
  Write-Host ''
  Write-Host "  $t" -ForegroundColor White
  Line
}
function Say([string]$m) { Write-Host "  $m" }
function Ok([string]$m) { Write-Host "  [ok] " -ForegroundColor Green -NoNewline; Write-Host $m }
function Warn([string]$m) { Write-Host "  [warn] " -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Fail([string]$m) { Write-Host "  [错误] " -ForegroundColor Red -NoNewline; Write-Host $m }
function Hint([string]$m) { Write-Host "         $m" -ForegroundColor DarkGray }

# 运行外部程序并返回退出码。
# 关键：脚本开头是 $ErrorActionPreference='Stop'，而 ssh/scp 失败时会把
# 错误写到 stderr，PowerShell 5.1 会把它当成终止性错误直接中断整个脚本
# （现场表现：刚打完"生成 SSH 密钥…"就跳到 pause）。这里临时放开这个设置。
function Invoke-Native {
  param([string]$Exe, [string[]]$ArgList, [switch]$Quiet)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    if ($Quiet) { & $Exe @ArgList 2>$null | Out-Null } else { & $Exe @ArgList }
  } catch { }
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  return $code
}

# ------------------------------------------------------------------ 网络
function Test-Port([string]$h, [int]$p, [int]$timeoutMs = 1200) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $ar = $client.BeginConnect($h, $p, $null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne($timeoutMs, $false)) { return $false }
    $client.EndConnect($ar)
    return $true
  } catch { return $false }
  finally { try { $client.Close() } catch { } }
}

function Get-LocalV4 {
  Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object IPAddress, InterfaceAlias
}

function Show-Network {
  $all = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object IPAddress, InterfaceAlias
  $inSubnet = $all | Where-Object { $_.IPAddress -like '192.168.1.*' }
  $wired = Get-WiredNic | Select-Object -First 1
  Say ''
  Say '本机网卡：'
  if ($all) {
    foreach ($i in $all) {
      $mark = if ($i.IPAddress -like '192.168.1.*') { '  <- 和机器狗同一网段' }
              elseif ($i.IPAddress -like '169.254.*') { '  <- 自动获取失败，没拿到 IP' }
              else { '' }
      Say ("    {0,-16} {1}{2}" -f $i.IPAddress, $i.InterfaceAlias, $mark)
    }
  } else { Say '    （没有可用的 IPv4 地址）' }
  Say ''
  if ($inSubnet) {
    Ok '本机已在 192.168.1.x 网段'
    if ($Wifi) { Hint "如需切换 WiFi：netsh wlan connect name=`"$Wifi`"" }
    return
  }
  if ($wired -and $wired.Status -ne 'Up') {
    Warn "有线网卡「$($wired.Name)」显示已断开 —— 网线没插好，或者机器狗还没通电"
    Say ''
    Say '请检查：'
    Say '  1) 网线一头插机器狗网口（不是充电口），另一头插电脑网口，听到咔哒一声'
    Say '  2) 机器狗通电开机，等 1 分钟（感知主机是 Jetson，启动慢）'
    Say '  3) 插好后运行 连接机器狗.bat，它会自动把本机网卡设成 192.168.1.50'
    Say ''
    Say '  小提示：网线插好后电脑网口指示灯会亮/闪；网卡状态会从「已断开」变成「已连接」'
    return
  }
  if ($wired) {
    Warn "网线已连接（$($wired.Name)），但这块网卡没有 192.168.1.x 地址"
    Say ''
    Say '机器狗不会自动分配 IP，把这块网卡设成静态地址就行：'
    Say '  · 直接重新运行 连接机器狗.bat（不要带 -Check），它会弹一次管理员授权自动设好'
    Say '  · 或手动：设置 → 网络和 Internet → 以太网 → IP 分配 → 编辑 → 手动 → IPv4'
    Say '           IP 192.168.1.50   掩码 255.255.255.0   网关留空'
    Say ''
    Say '设好之后本机应出现 192.168.1.50，再重新运行本脚本'
    return
  }
  Warn '本机不在 192.168.1.x 网段，现在连不到机器狗'
  Say ''
  Say '请做这件事：'
  Say '  1) 给机器狗开机（等 1 分钟左右，感知主机启动较慢）'
  Say '  2) 网线接到机器狗网口，或连它的 WiFi 热点'
  Say '  3) 网线的话本机设静态 IP 192.168.1.50；WiFi 的话选 Lite3-xxxx 热点'
  Say ''
  Say '  注意：连上机器狗网络后电脑会断网，这是正常的，它只提供局域网。'
}

function Wait-Robot([int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  $i = 0
  while ((Get-Date) -lt $deadline) {
    $i++
    if (Test-Port $RobotIp 22) { Write-Host ''; return $true }
    $left = [int]($deadline - (Get-Date)).TotalSeconds
    $spinner = '|/-\'[($i % 4)]
    Write-Host ("`r  等待机器狗 $RobotIp 上线… {0}  剩余 {1}s  " -f $spinner, $left) -NoNewline -ForegroundColor DarkGray
    Start-Sleep -Seconds 2
  }
  Write-Host ''
  return $false
}

function Confirm-Ssh {
  if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Fail '找不到 ssh 命令'
    Hint 'Windows 设置 → 应用 → 可选功能 → 添加功能 → OpenSSH 客户端'
    exit 1
  }
  if (-not (Get-Command scp -ErrorAction SilentlyContinue)) {
    Fail '找不到 scp 命令'; exit 1
  }
}

function Get-WiredNic {
  Get-NetAdapter -ErrorAction SilentlyContinue |
    Where-Object {
      $_.InterfaceDescription -notmatch 'VirtualBox|VMware|Hyper-V|Loopback|TAP|Wi-Fi Direct|Bluetooth' -and
      $_.InterfaceDescription -notmatch 'WiFi|Wi-Fi|802\.11|Wireless|WLAN'
    } |
    Sort-Object -Property @{ Expression = { if ($_.Status -eq 'Up') { 0 } else { 1 } } }
}

# 网线直连时机器狗不提供 DHCP，本机要有 192.168.1.x 才连得上
function Repair-StaticIp {
  $wired = Get-WiredNic | Select-Object -First 1
  if (-not $wired) { return $false }
  if ($wired.Status -ne 'Up') {
    Warn "有线网卡「$($wired.Name)」显示已断开：网线没插好，或机器狗还没通电"
    Say '  插好网线、给机器狗开机后再运行一次；也可以先设好 IP（网线插上就生效）'
    if ($Check) { return $false }
  }
  Say ''
  Say "把「$($wired.Name)」设成静态地址 192.168.1.50（机器狗不会自动分配 IP）"
  if ($Check) {
    Hint '去掉 -Check 重新运行，会自动提权帮你设好'
    return $false
  }
  Say ''
  Say '马上会弹一次管理员授权（UAC），点"是"就行'
  $helper = Join-Path $PSScriptRoot 'set_static_ip.ps1'
  if (-not (Test-Path $helper)) {
    Fail "缺少 $helper"
    return $false
  }
  try {
    $p = Start-Process -FilePath 'powershell' -Verb RunAs -Wait -PassThru -ArgumentList @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $helper, '-Enable'
    )
    if ($p.ExitCode -ne 0) {
      Warn "设置静态 IP 失败（退出码 $($p.ExitCode)）"
      return $false
    }
  } catch {
    Warn '没能提权设置 IP（可能点了"否"）'
    Say ''
    Say '手动设置：设置 → 网络和 Internet → 以太网 → IP 分配 → 编辑 → 手动 → IPv4'
    Say '          IP 192.168.1.50   掩码 255.255.255.0   网关留空'
    return $false
  }
  Start-Sleep -Seconds 2
  if ((Get-LocalV4) | Where-Object { $_.IPAddress -like '192.168.1.*' }) {
    Ok '网卡已经设成 192.168.1.50'
    return $true
  }
  Warn '设置完还是没看到 192.168.1.x 地址'
  return $false
}

# ------------------------------------------------------------------ 主流程
Title '绝影 Lite3 · 相机画面 —— 连接与部署'
Say "机器狗   $Target"
Say "服务地址 $Url"

Confirm-Ssh

if ($Wifi) {
  Say ''
  Say "切换到 WiFi：$Wifi"
  try { netsh wlan connect name="$Wifi" | Out-Null } catch { Warn '切换 WiFi 失败，请手动连接' }
  Start-Sleep -Seconds 3
}

if (-not (Test-Port $RobotIp 22)) {
  $hasSubnet = (Get-LocalV4) | Where-Object { $_.IPAddress -like '192.168.1.*' }
  if (-not $hasSubnet) {
    $wired = Get-WiredNic | Select-Object -First 1
    if ($wired -and $wired.Status -eq 'Up') { Repair-StaticIp | Out-Null }
  }
  if (-not (Test-Port $RobotIp 22)) {
    Show-Network
    Say ''
    if ($WaitSeconds -gt 0) {
      Say "会持续重试 $WaitSeconds 秒，你现在去插网线 / 连 WiFi 就行"
    }
    if (-not (Wait-Robot $WaitSeconds)) {
      Fail "还是连不上 $RobotIp"
      Say ''
      Say '排查顺序：'
      Say '  1) 机器狗通电了吗？感知主机要 1 分钟才启动完'
      Say '  2) 网线是不是插在机器狗网口上？插好后本机应出现 192.168.1.x 地址'
      Say '  3) WiFi 的话：电脑真的连到它的热点了吗？（看上面的网卡列表）'
      Say '  4) 换个办法：HDMI 接显示器进机器狗桌面，或网线直连后手动设 IP 192.168.1.50'
      Say '  5) 仍然不行就在机器狗上手动跑：bash ~/camera_web/tools/robot_setup.sh'
      exit 1
    }
  }
}
Ok "可以连到 $RobotIp（22 端口通）"

# ---------------- 只检查
if ($Check) {
  Say ''
  try {
    $h = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 4
    Ok "网页服务在运行：$Url"
  } catch {
    Warn "连得上机器狗，但网页服务没启动（或端口不是 $Port）"
    Hint '重新部署：双击 连接机器狗.bat'
  }
  Say ''
  exit 0
}

# ---------------- 停止
if ($Stop) {
  Say ''
  Say '停止机器狗上的服务…'
  $rc = Invoke-Native -Exe 'ssh' -ArgList @('-o','StrictHostKeyChecking=accept-new','-t',$Target,"pkill -f 'run.py --source' || true; echo stopped")
  if ($rc -eq 0) { Ok '已停止' } else { Warn "退出码 $rc" }
  Say ''
  exit 0
}

# ---------------- SSH 免密（只做一次，之后不再反复输密码）
$sshDir  = Join-Path $env:USERPROFILE '.ssh'
$keyFile = Join-Path $sshDir 'id_ed25519'
$pubFile = "$keyFile.pub"
if (-not (Test-Path $pubFile)) {
  Say ''
  Say '生成 SSH 密钥（只做一次，装好之后就不必再反复输密码）…'
  if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Force -Path $sshDir | Out-Null }
  & ssh-keygen -t ed25519 -N '""' -f $keyFile -C "lite3-camera-web@$env:COMPUTERNAME" | Out-Null
}
$pubKey = ''
if (Test-Path $pubFile) { $pubKey = (Get-Content -LiteralPath $pubFile -Raw).Trim() }

$keyOk = $false
if (Test-Path $keyFile) {
  $probe = Invoke-Native -Exe 'ssh' -Quiet -ArgList @('-o','BatchMode=yes','-o','ConnectTimeout=6','-o','StrictHostKeyChecking=accept-new',$Target,'true')
  if ($probe -eq 0) { $keyOk = $true }
}

if (-not $keyOk) {
  if ($pubKey) {
    Say ''
    Say '第一次连接机器狗：要输一次密码（一个英文单引号 '' ），装完免密以后就不用输了'
    $install = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && (grep -qF '$pubKey' ~/.ssh/authorized_keys 2>/dev/null || echo '$pubKey' >> ~/.ssh/authorized_keys) && chmod 600 ~/.ssh/authorized_keys && echo KEY_OK"
    $rc = Invoke-Native -Exe 'ssh' -ArgList @('-t','-o','StrictHostKeyChecking=accept-new',$Target,$install)
    if ($rc -eq 0) {
      $probe2 = Invoke-Native -Exe 'ssh' -Quiet -ArgList @('-o','BatchMode=yes','-o','ConnectTimeout=8',$Target,'true')
      if ($probe2 -eq 0) { $keyOk = $true; Ok '已配置免密登录，后面不再需要输密码' }
      else { Warn '免密没生效，下面还会再问密码，正常继续即可' }
    } else {
      Warn '这一轮没配上免密；下面每一步会各问一次密码，继续即可'
    }
  }
}

# ---------------- 打包
Say ''
Say '打包项目…'
$archive = Join-Path $env:TEMP 'lite3_camera_web.tgz'
if (Test-Path $archive) { Remove-Item $archive -Force }
& tar -czf $archive -C $Root --exclude=.deps --exclude=__pycache__ --exclude=docs --exclude='.edge-tmp*' --exclude=viewer.log .
if ($LASTEXITCODE -ne 0) { Fail '打包失败'; exit 1 }$mb = [math]::Round((Get-Item $archive).Length / 1MB, 2)
Ok "已打包 $archive（$mb MB）"

# ---------------- 上传
Say ''
Say '上传到机器狗…'
$remoteTgz = '/tmp/lite3_camera_web.tgz'
$rc = Invoke-Native -Exe 'scp' -ArgList @('-o','StrictHostKeyChecking=accept-new',$archive,"${Target}:$remoteTgz")
if ($rc -ne 0) {
  Fail '上传失败'
  Say ''
  Say '最常见的原因是密码不对：密码是一个英文单引号（撇号），不是双引号也不是逗号。'
  Say "如果提示密码错误，先按一下 Shift 切到英文输入法再输 '"
  Say '也可能 22 端口被防火墙挡了，或用户名不是 ysc。'
  exit 1
}
Ok '上传完成'

# ---------------- 远程安装并启动
Say ''
Say '在机器狗上解包、检查依赖并启动（若提示密码，仍是那个英文单引号 '' ）'
Line
$remoteCmd = "mkdir -p $RemoteDir && tar -xzf $remoteTgz -C $RemoteDir && rm -f $remoteTgz && bash $RemoteDir/tools/robot_setup.sh --source $Source --port $Port"
$code = Invoke-Native -Exe 'ssh' -ArgList @('-o','StrictHostKeyChecking=accept-new','-t',$Target,$remoteCmd)
Line

if ($code -ne 0) {
  Fail "远程启动失败（退出码 $code），看上面的输出"
  Say ''
  Say '常见原因：'
  Say '  · 缺 python3-opencv / pillow  → sudo apt install python3-opencv'
  Say '  · 缺 numpy                    → sudo apt install python3-numpy'
  Say '  · 密码输错导致 sudo 失败      → 重新运行本脚本'
  Say '  · 相机被占用                  → sudo systemctl stop realsense，或改用 -Source ros'
  Say ''
  Say '登录机器狗排查：'
  Say "  ssh $Target"
  Say "  tail -n 50 ~/camera_web/viewer.log"
  exit 1
}

# ---------------- 收尾
Say ''
try {
  $h = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 5
  Ok "服务自检通过：$Url"
} catch {
  Warn "还没响应，稍等几秒刷新 $Url"
}

Say ''
Say '完成！浏览器地址：'
Write-Host "  $Url" -ForegroundColor Green
Say ''
Say '以后每次只需要：'
Say '  双击 连接机器狗.bat          （重新部署并启动）'
Say '  连接机器狗.bat -Check        （看看还在不在）'
Say '  连接机器狗.bat -Stop         （停掉服务）'
Say '  手机/平板在同一 WiFi 下打开同一个地址也能看'

if (-not $NoOpen) {
  Start-Process $Url
}
Say ''
