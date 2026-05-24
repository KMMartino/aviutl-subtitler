param(
    [string]$InputPath = "test.m4a",
    [string]$Model = "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf",
    [string]$Mmproj = "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf",
    [string]$DiagnosticsDir = "diagnostics",
    [int]$ContextSize = 8192,
    [int]$GpuLayers = 99
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $InputPath)) {
    Write-Error "Input file not found: $InputPath"
}
if (-not (Test-Path -LiteralPath $Model)) {
    Write-Error "Model file not found: $Model"
}
if (-not (Test-Path -LiteralPath $Mmproj)) {
    Write-Error "Projector file not found: $Mmproj"
}

New-Item -ItemType Directory -Force -Path $DiagnosticsDir | Out-Null
$wavPath = Join-Path $DiagnosticsDir "test_16k_mono.wav"
$logPath = Join-Path $DiagnosticsDir "native_audio_diagnostic.txt"

ffmpeg -y -i $InputPath -map 0:a:0 -ac 1 -ar 16000 -vn -f wav $wavPath

$mtmd = Get-Command llama-mtmd-cli -ErrorAction SilentlyContinue
$mtmdPath = $null
if ($mtmd) {
    $mtmdPath = $mtmd.Source
} elseif (Test-Path -LiteralPath "C:\tools\llama-vulkan\llama-mtmd-cli.exe") {
    $mtmdPath = "C:\tools\llama-vulkan\llama-mtmd-cli.exe"
}

if (-not $mtmdPath) {
    $message = @"
Native llama.cpp multimodal CLI was not found.

Install or build a recent llama.cpp release that includes llama-mtmd-cli, then
rerun this diagnostic. This is needed to determine whether Gemma audio works
outside llama-cpp-python.

Converted WAV is available at:
$wavPath
"@
    $message | Tee-Object -FilePath $logPath
    exit 1
}

$prompt = "Transcribe the exact spoken words in this audio. Output only the transcript."

$cmd = @(
    "`"$mtmdPath`"",
    "-m", "`"$Model`"",
    "--mmproj", "`"$Mmproj`"",
    "--audio", "`"$wavPath`"",
    "-p", "`"$prompt`"",
    "-ngl", "$GpuLayers",
    "-c", "$ContextSize",
    "--jinja"
) -join " "

cmd.exe /d /c "$cmd 2>&1" | Tee-Object -FilePath $logPath

$exitCode = $LASTEXITCODE
"native_exit_code=$exitCode" | Tee-Object -FilePath $logPath -Append
exit $exitCode

