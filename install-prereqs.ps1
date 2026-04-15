$ErrorActionPreference = 'Stop'

function Test-WinFsp {
    (Test-Path 'HKLM:\Software\WinFsp') -or
    (Test-Path 'HKLM:\Software\WOW6432Node\WinFsp') -or
    (Test-Path 'C:\Program Files (x86)\WinFsp') -or
    (Test-Path 'C:\Program Files\WinFsp')
}

# --- WinFsp ---
if (Test-WinFsp) {
    Write-Host "WinFsp already installed."
} else {
    Write-Host "WinFsp not found. Fetching latest release from GitHub..."
    $api = 'https://api.github.com/repos/winfsp/winfsp/releases/latest'
    $release = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'rclone-tray-installer' }
    $asset = $release.assets | Where-Object { $_.name -like 'winfsp-*.msi' } | Select-Object -First 1
    if (-not $asset) { throw "Could not find WinFsp MSI in latest release." }
    $msi = Join-Path $env:TEMP $asset.name
    Write-Host "Downloading $($asset.name)..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $msi -UseBasicParsing
    Write-Host "Installing WinFsp (UAC prompt will appear)..."
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart" -Verb RunAs -Wait -PassThru
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {
        throw "WinFsp install failed with exit code $($p.ExitCode)."
    }
    if (-not (Test-WinFsp)) { throw "WinFsp install did not register." }
    Write-Host "WinFsp installed."
}

# --- rclone ---
$rcloneDir = Join-Path $env:LOCALAPPDATA 'Programs\rclone'
$rcloneExe = Join-Path $rcloneDir 'rclone.exe'
$onPath = Get-Command rclone -ErrorAction SilentlyContinue
if ($onPath) {
    Write-Host "rclone already on PATH at $($onPath.Source)."
} elseif (Test-Path $rcloneExe) {
    Write-Host "rclone already present at $rcloneExe."
} else {
    Write-Host "rclone not found. Downloading latest release..."
    $url = 'https://downloads.rclone.org/rclone-current-windows-amd64.zip'
    $zip = Join-Path $env:TEMP 'rclone.zip'
    $ext = Join-Path $env:TEMP 'rclone-extract'
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    if (Test-Path $ext) { Remove-Item $ext -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $ext -Force
    $src = Get-ChildItem $ext -Recurse -Filter rclone.exe | Select-Object -First 1
    if (-not $src) { throw "rclone.exe not found inside downloaded zip." }
    New-Item -ItemType Directory -Force -Path $rcloneDir | Out-Null
    Copy-Item $src.FullName $rcloneExe -Force
    Write-Host "Installed rclone at $rcloneExe."
}

# Ensure the rclone dir is on user PATH (persistent) and in this session
if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
    $userPath = [Environment]::GetEnvironmentVariable('PATH','User')
    if (-not $userPath -or ($userPath -notlike "*$rcloneDir*")) {
        $newPath = if ($userPath) { $userPath.TrimEnd(';') + ';' + $rcloneDir } else { $rcloneDir }
        [Environment]::SetEnvironmentVariable('PATH', $newPath, 'User')
        Write-Host "Added $rcloneDir to user PATH (takes effect for new shells)."
    }
    $env:PATH = "$rcloneDir;$env:PATH"
}

Write-Host "Prerequisites ready."
