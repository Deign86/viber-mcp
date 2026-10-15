# One-shot setup for viber-mcp on Windows. Run:  powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Add  -ServiceAccountKey <path to google_service_account.json>  to install the Drive key too.
param([string]$ServiceAccountKey)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Warning "uv installed but not on PATH yet. Close this window, open a new PowerShell, and rerun setup.ps1."; exit 1 }
}

Write-Host "Installing dependencies..."
uv sync

$dataDir = Join-Path $env:LOCALAPPDATA "viber-mcp"
$saKey = Join-Path $dataDir "google_service_account.json"
if ($ServiceAccountKey) {
    if (-not (Test-Path $ServiceAccountKey)) { Write-Warning "Service account key not found: $ServiceAccountKey"; exit 1 }
    $type = (Get-Content $ServiceAccountKey -Raw | ConvertFrom-Json).type
    if ($type -ne "service_account") { Write-Warning "$ServiceAccountKey is not a service account key (type: '$type')."; exit 1 }
    New-Item -ItemType Directory -Force $dataDir | Out-Null
    Copy-Item $ServiceAccountKey $saKey -Force
    Write-Host "Installed the Drive service account key to $saKey"
}
if (-not (Test-Path $saKey) -and -not (Test-Path google_oauth_client.json)) {
    Write-Host "Note: Google Drive isn't set up. Rerun with -ServiceAccountKey <path to google_service_account.json> (see README -> Google Drive setup)."
}

if (-not (Test-Path contacts.json)) {
    Copy-Item contacts.example.json contacts.json
    Write-Host "Created contacts.json with PLACEHOLDER numbers - edit it, or just use full +63... numbers."
}

if (-not (Get-Process Viber -ErrorAction SilentlyContinue)) {
    Write-Warning "Viber isn't running. Open Viber, sign in, open any chat, then rerun this script."
    exit 1
}

Write-Host "Running self-test (sends, types and focuses nothing)..."
uv run windows_selftest.py

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$server = Join-Path $PSScriptRoot "server.py"
$connected = @()

# --- Claude Code ---
if (Get-Command claude -ErrorAction SilentlyContinue) {
    claude mcp remove viber 2>$null
    claude mcp add --transport stdio --scope user viber -- "$py" "$server"
    $connected += "Claude Code"
}

# --- Claude Desktop (normal install + Microsoft Store install) ---
$cfgDirs = @(Join-Path $env:APPDATA "Claude")
$cfgDirs += Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages") -Filter "Claude_*" -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Join-Path $_.FullName "LocalCache\Roaming\Claude" }
foreach ($dir in $cfgDirs) {
    if (-not (Test-Path $dir)) { continue }
    $cfgPath = Join-Path $dir "claude_desktop_config.json"
    $cfg = [pscustomobject]@{}
    if (Test-Path $cfgPath) {
        Copy-Item $cfgPath "$cfgPath.bak" -Force
        $raw = Get-Content $cfgPath -Raw
        if ($raw -and $raw.Trim()) { $cfg = $raw | ConvertFrom-Json }
    }
    if (-not $cfg.PSObject.Properties["mcpServers"]) {
        $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
    }
    $entry = [pscustomobject]@{ command = $py; args = @($server) }
    $cfg.mcpServers | Add-Member -NotePropertyName viber -NotePropertyValue $entry -Force
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $cfgPath -Encoding UTF8
    $connected += "Claude Desktop ($cfgPath, backup saved as .bak)"
}

if ($connected.Count -eq 0) {
    Write-Warning "Neither Claude Code nor Claude Desktop was found, so nothing was connected. Install one and rerun."
} else {
    Write-Host "`nConnected to: $($connected -join '; ')"
    Write-Host "Fully QUIT and reopen Claude Desktop (tray icon > Quit) so it loads the viber server."
}
Write-Host "`nviber_capture.png shows what the server sees. It runs in the background - keep Viber open (it can be behind other windows)."
