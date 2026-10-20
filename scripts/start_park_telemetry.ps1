param([switch]$OpenDashboard, [switch]$InternetOnly, [string]$EspPort = '', [switch]$LocalOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($EspPort -and $EspPort -notmatch '^COM\d+$') { throw 'EspPort must be the ESP USB-UART COM port, e.g. COM8.' }
if ($LocalOnly -and $InternetOnly) { throw 'Use either -LocalOnly or -InternetOnly.' }
$root = Split-Path -Parent $PSScriptRoot
$gateway = Join-Path $root 'gateway\ev_gateway.py'
Write-Host "Telemetry project: $root"
# Do not silently reuse a server launched from another project copy.
$listeners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if (-not $owner -or $owner.CommandLine -notlike "*$gateway*") {
        throw 'Port 8766 belongs to another project or application. Stop that server before starting this project.'
    }
}
$toolRoot = Join-Path $env:USERPROFILE 'telemetry-tools'
$pythonCandidates = @(
    (Join-Path $toolRoot 'venv\Scripts\python.exe'),
    (Join-Path $toolRoot 'python\python.exe')
)
$pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pathPython -and $pathPython.Source -notlike '*\WindowsApps\*') { $pythonCandidates += $pathPython.Source }
$python = $null
foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        try {
            & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
        } catch { continue }
    }
}
if (-not $python) { throw 'Python 3.10+ is required. Install Python on PATH or create telemetry-tools\venv.' }
if ($EspPort) {
    $serialAvailable = $false
    try { & $python -c 'import serial' 2>$null; $serialAvailable = $LASTEXITCODE -eq 0 } catch {}
    if (-not $serialAvailable) { throw "ESP USB input requires pyserial. Run: & '$python' -m pip install pyserial" }
}
$ngrok = Join-Path $toolRoot 'ngrok\ngrok.exe'
$ngrokConfig = Join-Path $toolRoot 'ngrok\ngrok.yml'
$configPath = Join-Path $root 'firmware\esp32_ev_gateway\include\config.h'
$espBaud = 3000000
if ($EspPort -and (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    $baudMatch = [regex]::Match([IO.File]::ReadAllText($configPath), '(?m)^\s*#define\s+EV_USB_BAUD\s+(\d+)')
    if ($baudMatch.Success) { $espBaud = [int]$baudMatch.Groups[1].Value }
    if ($espBaud -lt 9600 -or $espBaud -gt 3000000) { throw 'EV_USB_BAUD must be between 9600 and 3000000.' }
}
$config = ''
if (-not $LocalOnly -and (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    $config = [IO.File]::ReadAllText($configPath)
}
function ReadDefine([string]$name) {
    $m = [regex]::Match($config, ('(?m)^\s*#define\s+{0}\s+"([^"]+)"' -f $name))
    if (-not $m.Success) { return '' }
    return $m.Groups[1].Value
}
$relayToken = ReadDefine 'EV_RELAY_TOKEN'
$relayUrl = ReadDefine 'EV_RELAY_URL'
$publicUrl = $relayUrl -replace '/api/vehicle/exchange$', ''
$settingsPath = Join-Path $toolRoot 'ev-relay.json'
if (-not $LocalOnly -and (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
    $settings = [IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
    if ($config -and $relayToken -notlike 'REPLACE_*' -and $relayToken -and
        ($relayToken -cne $settings.relay_token -or $publicUrl -ne $settings.public_url)) {
        throw 'ESP config.h and private PC relay settings differ. Run scripts/configure_ev_relay.ps1 to synchronize, then rebuild the ESP.'
    }
    $relayToken = $settings.relay_token
    $publicUrl = $settings.public_url
    $relayUrl = "$publicUrl/api/vehicle/exchange"
}
$relayUnavailable = ''
if ($LocalOnly) {
    $relayUnavailable = '-LocalOnly selected.'
} elseif (-not $config -and -not $relayToken) {
    $relayUnavailable = 'Private config.h is missing. Internet relay needs the ESP relay URL/token from your local configuration.'
} elseif ($relayToken.Length -lt 16 -or $relayToken -like 'REPLACE_*' -or $publicUrl -like '*YOUR-*' -or -not $publicUrl) {
    $relayUnavailable = 'Private relay URL/token are not configured in config.h; example values cannot be used.'
} elseif ($relayUrl -notmatch '^https://[^/\s]+/api/vehicle/exchange$') {
    throw 'EV_RELAY_URL must be a verified HTTPS endpoint ending in /api/vehicle/exchange.'
} elseif (-not (Test-Path -LiteralPath $ngrok -PathType Leaf) -or -not (Test-Path -LiteralPath $ngrokConfig -PathType Leaf)) {
    $relayUnavailable = 'Internet relay needs telemetry-tools\ngrok\ngrok.exe and ngrok.yml.'
}
$relayEnabled = -not [bool]$relayUnavailable
if (-not $relayEnabled) {
    if ($InternetOnly) { throw "Internet-only startup is unavailable: $relayUnavailable" }
    Write-Warning "$relayUnavailable Starting the local dashboard (USB / local UDP)."
    $relayToken = ''
}
New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
$existing = $null
try { $existing = Invoke-RestMethod 'http://127.0.0.1:8766/api/telemetry' -TimeoutSec 2 } catch {}
$inputMismatch = $existing -and $EspPort -and ($existing.input_mode -ne 'ESP_USB' -or $existing.input_port -ne $EspPort)
$relayMismatch = $existing -and [bool]$existing.relay_server_enabled -ne $relayEnabled
$tokenMismatch = $false
if ($existing -and $relayEnabled) {
    # Invalid JSON checks authentication without creating a telemetry sample.
    try {
        Invoke-WebRequest 'http://127.0.0.1:8766/api/vehicle/exchange' -Method Post -UseBasicParsing `
            -Headers @{ Authorization = "Bearer $relayToken" } -ContentType 'application/json' -Body 'not-json' -TimeoutSec 2 | Out-Null
        throw 'Unexpected relay authentication probe response.'
    } catch {
        $response = $_.Exception.Response
        if (-not $response) { throw }
        $status = [int]$response.StatusCode
        if ($status -eq 401) { $tokenMismatch = $true }
        elseif ($status -ne 400) { throw 'Cannot verify the running gateway relay authentication.' }
    }
}
if ($inputMismatch -or $relayMismatch -or $tokenMismatch -or ($existing -and -not $existing.gateway_control_enabled)) {
    if ($existing.recording_active) { throw 'A measurement is recording. Stop it in the dashboard before changing gateway mode.' }
    $listeners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction Stop)
    foreach ($listener in $listeners) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if (-not $owner -or $owner.CommandLine -notlike "*$gateway*") {
            throw 'Port 8766 belongs to another project or application. It was not stopped.'
        }
        $process = [Diagnostics.Process]::GetProcessById([int]$owner.ProcessId)
        $process.Kill()
        if (-not $process.WaitForExit(5000)) { throw 'The previous gateway did not exit within five seconds.' }
    }
    $existing = $null
}
if ($null -eq $existing) {
    $saved = $env:DJY_EV_RELAY_TOKEN
    $env:DJY_EV_RELAY_TOKEN = $relayToken
    try {
        $gateway = Join-Path $root 'gateway\ev_gateway.py'
        $gatewayArgs = @('-u', ('"{0}"' -f $gateway), '--http-host', '127.0.0.1', '--enable-control')
        if ($EspPort) { $gatewayArgs += @('--serial', $EspPort, '--baud', $espBaud) }
        if ($InternetOnly) { $gatewayArgs += @('--listen-host', '127.0.0.1') }
        Start-Process -FilePath $python -WindowStyle Hidden -WorkingDirectory $root `
            -ArgumentList $gatewayArgs `
            -RedirectStandardOutput (Join-Path $toolRoot 'gateway.log') `
            -RedirectStandardError (Join-Path $toolRoot 'gateway-error.log')
    } finally { $env:DJY_EV_RELAY_TOKEN = $saved }
}
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $state = Invoke-RestMethod 'http://127.0.0.1:8766/api/telemetry' -TimeoutSec 1
        if ([bool]$state.relay_server_enabled -eq $relayEnabled) { $ready = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 250
}
if (-not $ready) { throw 'Gateway did not start in the requested mode; inspect telemetry-tools\gateway-error.log.' }
if (-not $state.gateway_control_enabled) {
    throw 'An existing read-only gateway owns port 8766. Stop it explicitly before starting bidirectional telemetry.'
}
if ($relayEnabled) {
    $tunnel = $null
    try {
        $agent = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 2
        $tunnel = $agent.tunnels | Where-Object { $_.public_url -eq $publicUrl -and $_.config.addr -match ':8766$' }
        if (-not $tunnel -and $agent.tunnels.Count -gt 0) { throw 'A different ngrok tunnel is running. It was not stopped.' }
    } catch { if ($_.Exception.Message -like 'A different*') { throw } }
    if (-not $tunnel) {
        Start-Process -FilePath $ngrok -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $toolRoot 'ngrok\agent-stdout.log') `
            -RedirectStandardError (Join-Path $toolRoot 'ngrok\agent-error.log') -ArgumentList @(
            'http', '8766', '--url', $publicUrl, '--config', ('"{0}"' -f $ngrokConfig),
            '--log', ('"{0}"' -f (Join-Path $toolRoot 'ngrok\agent.log')))
        for ($attempt = 0; $attempt -lt 30 -and -not $tunnel; $attempt++) {
            Start-Sleep -Milliseconds 300
            try {
                $agent = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 1
                $tunnel = $agent.tunnels | Where-Object { $_.public_url -eq $publicUrl -and $_.config.addr -match ':8766$' }
            } catch {}
        }
    }
    if (-not $tunnel) {
        $message = 'ngrok did not connect. Check internet access and telemetry-tools\ngrok\agent.log; the local dashboard is available.'
        if ($InternetOnly) { throw $message }
        Write-Warning $message
    }
}
Write-Host 'PC dashboard: http://127.0.0.1:8766/pit'
Write-Host 'Onboard HUD: http://127.0.0.1:8766/onboard'
if ($EspPort) { Write-Host "ESP USB-UART: $EspPort, $espBaud baud; wireless samples are de-duplicated." }
if ($relayEnabled -and $tunnel) {
    foreach ($page in @('pit', 'battery', 'records', 'map', 'phone', 'driver', 'diagnostic', 'onboard')) {
        Write-Host ("Internet {0}: {1}/{0}" -f $page, $publicUrl)
    }
    Write-Host "ESP relay: $publicUrl/api/vehicle/exchange"
    Write-Host "Phone GPS receiver: $publicUrl/api/gnss/native"
}
elseif ($relayEnabled) { Write-Host 'Internet tunnel: OFFLINE. Run the launcher again to retry.' }
else { Write-Host 'Internet relay: OFF. Local UDP and ESP USB input do not require config.h or ngrok.' }
Write-Host 'Vehicle commands require the local pit controls; public control requests are refused.'
if ($OpenDashboard) { Start-Process 'http://127.0.0.1:8766/pit' }
