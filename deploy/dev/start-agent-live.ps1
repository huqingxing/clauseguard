#Requires -Version 5.1
<#
启动 python-agent（live 模式，走阿里云百炼 qwen-plus）

用法（任意目录都可以，脚本自己定位仓库根）：
    pwsh deploy\dev\start-agent-live.ps1
  或
    powershell -NoProfile -ExecutionPolicy Bypass -File deploy\dev\start-agent-live.ps1

前置：
  1. 用户环境变量 CLAUSEGUARD_AGENT_LLM_API_KEY 已设置（脚本只读不写）
  2. Java 已在 8080 起好（没有也能起，但提交审查会连不上网关）
#>

$ErrorActionPreference = 'Stop'

# ---------- 定位仓库根（本脚本在 deploy/dev/ 下） ----------
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$agentDir = Join-Path $root 'python-agent'
if (-not (Test-Path (Join-Path $agentDir 'app\main.py'))) {
    Write-Host "[X] 找不到 python-agent 目录：$agentDir" -ForegroundColor Red
    exit 1
}

function Test-LocalPort([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(500)
        return ($ok -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# ---------- 密钥检查（fail fast，不做静默降级） ----------
$key = $env:CLAUSEGUARD_AGENT_LLM_API_KEY
if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Host ''
    Write-Host "[X] 没有找到环境变量 CLAUSEGUARD_AGENT_LLM_API_KEY" -ForegroundColor Red
    Write-Host '    设置一次即可（新开窗口生效）：'
    Write-Host '      setx CLAUSEGUARD_AGENT_LLM_API_KEY "sk-你的key"'
    Write-Host ''
    exit 1
}
$masked = $key.Substring(0, [Math]::Min(4, $key.Length)) + '****' + $key.Substring([Math]::Max(0, $key.Length - 4))

# ---------- 环境变量（只设非密钥项） ----------
$env:CLAUSEGUARD_AGENT_MODEL_BACKEND  = 'live'
$env:CLAUSEGUARD_AGENT_LLM_BASE_URL   = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:CLAUSEGUARD_AGENT_LLM_MODEL      = 'qwen-plus'
# 本地联调不用 Postgres checkpoint 时置空；需要跨进程恢复审批态就填真实 DSN
$env:CLAUSEGUARD_AGENT_PG_DSN         = ''
# 本机若开了系统代理，Python 调 Java 会走代理导致 502
$env:NO_PROXY   = 'localhost,127.0.0.1'
$env:HTTP_PROXY  = ''
$env:HTTPS_PROXY = ''

# ---------- 选择解释器：优先项目 .venv，其次 uv ----------
$venvPy = Join-Path $agentDir '.venv\Scripts\python.exe'
if (Test-Path $venvPy) {
    $exe  = $venvPy
    $args = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000')
} else {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Host "[X] 既没有 .venv，也没有 uv。先在 python-agent 下执行：uv sync" -ForegroundColor Red
        exit 1
    }
    $exe  = $uv.Source
    $args = @('run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000')
}

# ---------- 启动横幅 ----------
Write-Host ''
Write-Host '========== python-agent / LIVE ==========' -ForegroundColor Cyan
Write-Host ("  模型      : qwen-plus  (DashScope 兼容模式)")
Write-Host ("  地址      : https://dashscope.aliyuncs.com/compatible-mode/v1")
Write-Host ("  Key       : $masked")
Write-Host ("  解释器    : $exe")
Write-Host ("  工作目录  : $agentDir")
if (Test-LocalPort 8080) {
    Write-Host  '  Java:8080 : 已就绪' -ForegroundColor Green
} else {
    Write-Host  '  Java:8080 : 未监听（能起服务，但提交审查会失败，先启动 Java）' -ForegroundColor Yellow
}
Write-Host '  监听      : http://127.0.0.1:8000'
Write-Host '  停止      : Ctrl + C'
Write-Host '=========================================' -ForegroundColor Cyan
Write-Host ''

Set-Location $agentDir
& $exe @args
