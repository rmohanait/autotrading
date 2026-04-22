$ErrorActionPreference = "Stop"

Write-Host "Ripster Trader - Service Installer" -ForegroundColor Cyan
Write-Host ""

if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Host "ERROR: Please run as Administrator." -ForegroundColor Red
    pause
    exit 1
}

$TradingDir  = "D:\Cowork\AutoTrading"
$NssmDir     = "C:\nssm"
$NssmExe     = "$NssmDir\nssm-2.24\win64\nssm.exe"
$ServiceName = "RipsterTrader"
$PythonPath  = (Get-Command python -ErrorAction SilentlyContinue).Source

if (-not $PythonPath) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $PythonPath = $c
            break
        }
    }
}

Write-Host "Python : $PythonPath" -ForegroundColor Green
Write-Host "Dir    : $TradingDir" -ForegroundColor Green
Write-Host ""

if (-not (Test-Path $PythonPath)) {
    Write-Host "ERROR: python.exe not found." -ForegroundColor Red
    pause
    exit 1
}

Write-Host "[1/4] Downloading NSSM..." -ForegroundColor Yellow
if (-not (Test-Path $NssmExe)) {
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "C:\nssm.zip" -UseBasicParsing
    Expand-Archive -Path "C:\nssm.zip" -DestinationPath $NssmDir -Force
    Remove-Item "C:\nssm.zip"
    Write-Host "NSSM downloaded." -ForegroundColor Green
} else {
    Write-Host "NSSM already present." -ForegroundColor Green
}

Write-Host "[2/4] Removing old service if exists..." -ForegroundColor Yellow
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    & $NssmExe stop $ServiceName 2>$null
    & $NssmExe remove $ServiceName confirm
    Start-Sleep -Seconds 2
}

Write-Host "[3/4] Installing service..." -ForegroundColor Yellow
& $NssmExe install $ServiceName $PythonPath "main.py"
& $NssmExe set $ServiceName AppDirectory $TradingDir
& $NssmExe set $ServiceName AppStdout "$TradingDir\service.log"
& $NssmExe set $ServiceName AppStderr "$TradingDir\service.log"
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateBytes 10485760
& $NssmExe set $ServiceName AppRestartDelay 15000
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

Write-Host "[4/4] Starting service..." -ForegroundColor Yellow
& $NssmExe start $ServiceName
Start-Sleep -Seconds 3

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "SUCCESS! RipsterTrader is running as a Windows service." -ForegroundColor Green
    Write-Host "It will auto-start on every reboot." -ForegroundColor Green
    Write-Host ""
    Write-Host "View live logs : Get-Content D:\Cowork\AutoTrading\service.log -Wait"
    Write-Host "Stop service   : Stop-Service RipsterTrader"
    Write-Host "Start service  : Start-Service RipsterTrader"
} else {
    Write-Host "WARNING: Service may not have started. Check logs:" -ForegroundColor Yellow
    Write-Host "Get-Content D:\Cowork\AutoTrading\service.log -Tail 30"
}

pause
