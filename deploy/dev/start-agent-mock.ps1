#Requires -Version 5.1
<#
启动 python-agent（mock 模式，确定性替身，不调真实模型）

用法（任意目录都可以，脚本自己定位仓库根）：
    pwsh deploy\dev\start-agent-mock.ps1
  或
    powershell -NoProfile -ExecutionPolicy Bypass -File deploy\dev\start-agent-mock.ps1

用途：演示链路 / 压测 / 无密钥环境。findings 的 summary 全是 "[mock] ..."，
      只能证明编排与流式通了，不能用来判断审查质量。
#>

$ErrorActionPreference = 'Stop'

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

# ---------- 环境变量（不需要密钥） ----------
$env:CLAUSEGUARD_AGENT_MODEL_BACKEND = 'mock'
$env:CLAUSEGUARD_AGENT_PG_DSN        = ''
# 共享存储根：与 java-service 默认上传根（./uploads，相对其启动目录）对齐；
# Java 上传源落点 = 此处 + contracts/{id}/v{n}/source.docx，Python render 据此取源
# （见 contracts/storage-keys.md）。这是「下载批注稿」默认链路接通的关键。
$env:CLAUSEGUARD_MINIO_LOCAL_DIR     = Join-Path $root 'java-service' 'uploads'
$env:NO_PROXY    = 'localhost,127.0.0.1'
$env:HTTP_PROXY  = ''
$env:HTTPS_PROXY = ''

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

Write-Host ''
Write-Host '========== python-agent / MOCK ==========' -ForegroundColor DarkYellow
Write-Host  '  模型      : 确定性替身（不调 API，无网络请求）'
Write-Host  '  产出      : findings.summary 全为 [mock] 占位'
Write-Host ("  解释器    : $exe")
if (Test-LocalPort 8080) {
    Write-Host  '  Java:8080 : 已就绪' -ForegroundColor Green
} else {
    Write-Host  '  Java:8080 : 未监听（能起服务，但提交审查会失败）' -ForegroundColor Yellow
}
Write-Host  '  监听      : http://127.0.0.1:8000'
Write-Host  '  停止      : Ctrl + C'
Write-Host '=========================================' -ForegroundColor DarkYellow
Write-Host ''

Set-Location $agentDir
& $exe @args
