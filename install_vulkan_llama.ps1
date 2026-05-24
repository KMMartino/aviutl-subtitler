param(
    [string]$InstallDir = "C:\tools\llama-vulkan",
    [string]$ZipPath = "$HOME\Downloads\llama-vulkan.zip"
)

$ErrorActionPreference = "Stop"

if (Get-Command vulkaninfo -ErrorAction SilentlyContinue) {
    vulkaninfo | Out-Null
    Write-Host "Vulkan runtime detected."
} else {
    Write-Warning "vulkaninfo was not found. Continuing, but Vulkan runtime/GPU visibility was not verified."
}

Write-Host "Resolving latest llama.cpp release..."
$latestRelease = (Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest").tag_name
if (-not $latestRelease) {
    throw "Could not determine latest llama.cpp release tag."
}

$downloadUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$latestRelease/llama-$latestRelease-bin-win-vulkan-x64.zip"
$zipDir = Split-Path -Parent $ZipPath
if ($zipDir) {
    New-Item -ItemType Directory -Force -Path $zipDir | Out-Null
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host "Downloading $downloadUrl"
Invoke-WebRequest -Uri $downloadUrl -OutFile $ZipPath

Write-Host "Extracting to $InstallDir"
Expand-Archive -Path $ZipPath -DestinationPath $InstallDir -Force

$serverPath = Join-Path $InstallDir "llama-server.exe"
$mtmdPath = Join-Path $InstallDir "llama-mtmd-cli.exe"

if (-not (Test-Path -LiteralPath $serverPath)) {
    throw "Install completed, but llama-server.exe was not found at $serverPath"
}
if (-not (Test-Path -LiteralPath $mtmdPath)) {
    throw "Install completed, but llama-mtmd-cli.exe was not found at $mtmdPath"
}

Write-Host "Installed llama.cpp $latestRelease Vulkan build:"
Write-Host "  $serverPath"
Write-Host "  $mtmdPath"
