param([string]$PublicUrl = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $env:USERPROFILE 'telemetry-tools'
$settingsPath = Join-Path $toolRoot 'ev-relay.json'
$configPath = Join-Path $root 'firmware\esp32_ev_gateway\include\config.h'
$templatePath = Join-Path $root 'firmware\esp32_ev_gateway\include\config.example.h'
$backupConfigPath = Join-Path $toolRoot 'esp-config.h'
$saved = $null
if (Test-Path -LiteralPath $settingsPath) {
    $saved = [IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
}
$text = if (Test-Path -LiteralPath $configPath) {
    [IO.File]::ReadAllText($configPath)
} elseif (Test-Path -LiteralPath $backupConfigPath) {
    [IO.File]::ReadAllText($backupConfigPath)
} else {
    [IO.File]::ReadAllText($templatePath)
}
function ReadSetting([string]$name) {
    $match = [regex]::Match($text, ('(?m)^\s*#define\s+{0}\s+"([^"]*)"' -f $name))
    if ($match.Success) { return $match.Groups[1].Value }
    return ''
}
if (-not $PublicUrl -and $saved) { $PublicUrl = $saved.public_url }
if (-not $PublicUrl) { $PublicUrl = (ReadSetting 'EV_RELAY_URL') -replace '/api/vehicle/exchange$', '' }
$PublicUrl = $PublicUrl.TrimEnd('/')
if ($PublicUrl -notmatch '^https://[a-zA-Z0-9.-]+$' -or $PublicUrl -like '*YOUR-*') {
    throw 'Provide the actual ngrok HTTPS domain: -PublicUrl https://your-domain.ngrok-free.dev'
}
$token = if ($saved) { $saved.relay_token } else { ReadSetting 'EV_RELAY_TOKEN' }
if ($token.Length -lt 16 -or $token -like 'REPLACE_*') {
    $bytes = [byte[]]::new(32)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = -join ($bytes | ForEach-Object { $_.ToString('x2') })
}
foreach ($entry in @(
    @{ Name = 'EV_RELAY_ENABLED'; Value = '1' },
    @{ Name = 'EV_RELAY_URL'; Value = ('"{0}/api/vehicle/exchange"' -f $PublicUrl) },
    @{ Name = 'EV_RELAY_TOKEN'; Value = ('"{0}"' -f $token) }
)) {
    $pattern = '(?m)^([ \t]*#define[ \t]+{0}[ \t]+)[^\r\n]+' -f $entry.Name
    if (-not [regex]::IsMatch($text, $pattern)) { throw "Missing setting: $($entry.Name)" }
    $value = $entry.Value
    $text = [regex]::Replace($text, $pattern, [Text.RegularExpressions.MatchEvaluator]{ param($m) $m.Groups[1].Value + $value })
}
New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
$settings = @{ public_url = $PublicUrl; relay_token = $token } | ConvertTo-Json
[IO.File]::WriteAllText($settingsPath, $settings, $utf8)
[IO.File]::WriteAllText($configPath, $text, $utf8)
[IO.File]::WriteAllText($backupConfigPath, $text, $utf8)
$linksDir = Join-Path $root '.local'
New-Item -ItemType Directory -Path $linksDir -Force | Out-Null
$links = @(
    '# Telemetry connection links', '',
    'Generated from the private relay settings. Re-run configure_ev_relay.ps1 when the domain changes.', '',
    '| Page | Internet | This PC |', '|---|---|---|'
)
foreach ($page in @('pit', 'battery', 'records', 'map', 'phone', 'driver', 'diagnostic', 'onboard')) {
    $links += "| $page | [$publicUrl/$page]($publicUrl/$page) | [http://127.0.0.1:8766/$page](http://127.0.0.1:8766/$page) |"
}
$links += @('', "ESP relay (authenticated POST): $PublicUrl/api/vehicle/exchange",
    "Phone GPS receiver: $PublicUrl/api/gnss/native", "Health: $PublicUrl/health", '',
    'Start the server: start_telemetry.bat -InternetOnly',
    'Verify: python scripts/check_ev_relay.py',
    'ESP URL/token changes require rebuilding and uploading the firmware.')
[IO.File]::WriteAllLines((Join-Path $linksDir 'connection-links.md'), $links, $utf8)
Write-Host "Relay domain: $PublicUrl"
Write-Host "All page links: $linksDir\connection-links.md"
Write-Host 'Private PC settings saved outside the project; ESP config.h uses the same relay token.'
Write-Host 'Rebuild and upload the ESP firmware to apply these settings to the board.'
