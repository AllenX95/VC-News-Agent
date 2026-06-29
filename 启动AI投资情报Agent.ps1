param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$BaseBackendPort = 8011
$MaxBackendPort = 8020
$FrontendPort = 5173
$FrontendBaseUrl = "http://127.0.0.1:$FrontendPort"
$AppUrl = "$FrontendBaseUrl/#/"
$LogDir = Join-Path $Root "logs"
$BackendPortFile = Join-Path $LogDir "web-backend.port"

function Wait-ForKey {
    Write-Host ""
    Read-Host "Press Enter to close"
}

function Test-PortOpen {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(400, $false)
        if ($connected) {
            $client.EndConnect($async)
        }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Test-AgentBackend {
    param([int]$Port)
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/app-info" -TimeoutSec 2
        return $response.app_id -eq "ai-investment-agent"
    } catch {
        return $false
    }
}

function Find-AgentBackendPort {
    for ($port = $BaseBackendPort; $port -le $MaxBackendPort; $port++) {
        if (Test-AgentBackend -Port $port) {
            return $port
        }
    }
    return 0
}

function Find-FreeBackendPort {
    if (Test-Path -LiteralPath $BackendPortFile) {
        $savedPort = 0
        if ([int]::TryParse((Get-Content -LiteralPath $BackendPortFile -Raw).Trim(), [ref]$savedPort)) {
            if ($savedPort -ge $BaseBackendPort -and $savedPort -le $MaxBackendPort -and -not (Test-PortOpen -Port $savedPort)) {
                return $savedPort
            }
        }
    }

    for ($port = $BaseBackendPort; $port -le $MaxBackendPort; $port++) {
        if (-not (Test-PortOpen -Port $port)) {
            return $port
        }
    }
    return 0
}

function Test-VueFrontend {
    try {
        $response = Invoke-WebRequest -Uri $FrontendBaseUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content.Contains('/src/main.ts')
    } catch {
        return $false
    }
}

function Wait-UntilReady {
    param(
        [scriptblock]$Probe,
        [int]$Attempts = 60
    )
    for ($i = 0; $i -lt $Attempts; $i++) {
        if (& $Probe) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-HiddenCommand {
    param([string]$Command)

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $env:ComSpec
    $startInfo.Arguments = "/d /c $Command"
    $startInfo.WorkingDirectory = $Root
    $startInfo.UseShellExecute = $true
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw "Failed to start background process"
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Python environment not found: $Python" -ForegroundColor Red
    Write-Host "Keep the .venv folder or reinstall Python dependencies before starting."
    Wait-ForKey
    exit 1
}

$Npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if ($null -eq $Npm) {
    Write-Host "npm.cmd was not found. Install Node.js and reopen this script." -ForegroundColor Red
    Wait-ForKey
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $Root "node_modules\vite\package.json"))) {
    Write-Host "Frontend dependencies are missing. Run npm install first." -ForegroundColor Red
    Wait-ForKey
    exit 1
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$BackendPort = Find-AgentBackendPort
if ($BackendPort -eq 0) {
    $BackendPort = Find-FreeBackendPort
    if ($BackendPort -eq 0) {
        Write-Host "Ports $BaseBackendPort-$MaxBackendPort are occupied." -ForegroundColor Red
        Write-Host "Close one of the applications using those ports and run this launcher again."
        Wait-ForKey
        exit 1
    }

    Write-Host "Starting backend on port $BackendPort..."
    $backendOut = Join-Path $LogDir "web-backend.out.log"
    $backendErr = Join-Path $LogDir "web-backend.err.log"
    $backendCommand = "set `"VC_NEWS_DISABLE_STARTUP_CATCHUP=1`" && `"$Python`" -B app.py --port $BackendPort --no-open-browser 1>>`"$backendOut`" 2>>`"$backendErr`""
    Start-HiddenCommand -Command $backendCommand

    if (-not (Wait-UntilReady -Probe { Test-AgentBackend -Port $BackendPort })) {
        Write-Host "Backend startup timed out." -ForegroundColor Red
        Write-Host "See logs\web-backend.err.log for details."
        Wait-ForKey
        exit 1
    }
} else {
    Write-Host "Reusing backend on port $BackendPort."
}

Set-Content -LiteralPath $BackendPortFile -Value $BackendPort -Encoding ASCII
$BackendBaseUrl = "http://127.0.0.1:$BackendPort"

if (-not (Test-VueFrontend)) {
    if (Test-PortOpen -Port $FrontendPort) {
        Write-Host "Port $FrontendPort is occupied by another application." -ForegroundColor Red
        Write-Host "Close that application and run this launcher again."
        Wait-ForKey
        exit 1
    }

    Write-Host "Starting web frontend on port $FrontendPort..."
    $frontendOut = Join-Path $LogDir "web-frontend.out.log"
    $frontendErr = Join-Path $LogDir "web-frontend.err.log"
    $frontendCommand = "set `"VITE_API_BASE_URL=$BackendBaseUrl`" && npm run frontend:dev 1>>`"$frontendOut`" 2>>`"$frontendErr`""
    Start-HiddenCommand -Command $frontendCommand

    if (-not (Wait-UntilReady -Probe { Test-VueFrontend })) {
        Write-Host "Web frontend startup timed out." -ForegroundColor Red
        Write-Host "See logs\web-frontend.err.log for details."
        Wait-ForKey
        exit 1
    }
} else {
    Write-Host "Reusing web frontend on port $FrontendPort."
}

if ($NoBrowser) {
    Write-Host "Web application is ready at $AppUrl" -ForegroundColor Green
} else {
    Write-Host "Opening $AppUrl" -ForegroundColor Green
    Start-Process $AppUrl
}
