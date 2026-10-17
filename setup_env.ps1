# 一键搭建运行环境（Windows PowerShell）
#
# 用法：在本文件所在目录执行
#   .\setup_env.ps1
# 若提示脚本被禁止运行，先执行：
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir

Write-Host '=== 植物生态效益计算器 环境搭建 ===' -ForegroundColor Cyan

# 1. 检查 Python
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Host '[x] 找不到 python 命令。请先安装 Python 3.12。' -ForegroundColor Red
    exit 1
}
$ver = & python --version 2>&1
Write-Host "[1/4] 检测到 $ver" -ForegroundColor Green
Write-Host "      解释器: $python"

# 2. 创建虚拟环境（已存在则跳过）
if (Test-Path '.venv\Scripts\python.exe') {
    Write-Host '[2/4] .venv 已存在，跳过创建' -ForegroundColor Yellow
} else {
    Write-Host '[2/4] 创建虚拟环境 .venv ...' -ForegroundColor Green
    python -m venv .venv
}

# 3. 安装依赖（走清华镜像加速）
Write-Host '[3/4] 安装依赖（使用清华镜像）...' -ForegroundColor Green
& '.venv\Scripts\python.exe' -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host '[x] 依赖安装失败，请检查网络后重试。' -ForegroundColor Red
    exit 1
}

# 4. 自检
Write-Host '[4/4] 环境自检 ...' -ForegroundColor Green
& '.venv\Scripts\python.exe' -c @'
import sys
print("  Python   :", sys.version.split()[0])
import streamlit, pandas, plotly, requests
print("  streamlit:", streamlit.__version__)
print("  pandas   :", pandas.__version__)
print("  plotly   :", plotly.__version__)
print("  requests :", requests.__version__)
df = pandas.read_csv("data/plants.csv")
print("  plants.csv:", df.shape, "->", list(df.columns))
print("  [OK] 环境就绪")
'@

Write-Host ''
Write-Host '启动应用：' -ForegroundColor Cyan
Write-Host '  .\.venv\Scripts\Activate.ps1'
Write-Host '  streamlit run app.py'
