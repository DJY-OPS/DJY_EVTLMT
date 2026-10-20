param([switch]$Upload, [string]$Port = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Port -and $Port -notmatch '^COM\d+$') { throw 'Upload requires the ESP USB-UART port: -Upload -Port COM8 (use the actual port).' }
$root = Split-Path -Parent $PSScriptRoot
$pio = Join-Path $env:USERPROFILE 'telemetry-tools\venv\Scripts\pio.exe'
if (-not (Test-Path -LiteralPath $pio)) {
    $command = Get-Command pio.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw 'PlatformIO is not installed. Install it in telemetry-tools\venv or on PATH.' }
    $pio = $command.Source
}
if ($Upload -and -not $Port) {
    $serialPython = Join-Path (Split-Path -Parent $pio) 'python.exe'
    if (-not (Test-Path -LiteralPath $serialPython)) { throw 'Specify the ESP USB-UART port with -Port COM8.' }
    $ports = @(& $serialPython -c "import serial.tools.list_ports; print('\n'.join(p.device for p in serial.tools.list_ports.comports() if p.vid in (0x10c4, 0x1a86, 0x303a, 0x0403)))")
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect USB serial ports. Install pyserial in the PlatformIO Python environment.' }
    $ports = @($ports | Where-Object { $_ -match '^COM\d+$' })
    if ($ports.Count -ne 1) { throw 'Connect one ESP USB-UART board, or specify the actual port with -Port COM8.' }
    $Port = $ports[0]
    Write-Host "Detected ESP USB-UART port: $Port"
}
& (Join-Path $PSScriptRoot 'configure_ev_relay.ps1')
$firmwareRoot = Join-Path $root 'firmware\esp32_ev_gateway'
$arguments = @('run', '--project-dir', $firmwareRoot, '-c', (Join-Path $firmwareRoot 'platformio-modern.ini'))
if ($Upload) { $arguments += @('--target', 'upload', '--upload-port', $Port) }
$savedPythonUtf8 = $env:PYTHONUTF8
$savedPythonEncoding = $env:PYTHONIOENCODING
try {
    # esptool progress bars contain Unicode block characters not supported by CP949.
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    & $pio @arguments
    if ($LASTEXITCODE -ne 0) { throw 'ESP build/upload failed. See the PlatformIO output above.' }
} finally {
    $env:PYTHONUTF8 = $savedPythonUtf8
    $env:PYTHONIOENCODING = $savedPythonEncoding
}
