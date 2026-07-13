param(
    [string]$Executable = "C:\tools\personal\Subtitler-latest\SubUtl.exe",
    [string]$DirectExecutable = (Join-Path $PSScriptRoot "..\..\release\win-unpacked\SubUtl.exe"),
    [switch]$SkipPortable,
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$resolvedPortableExecutable = if ($SkipPortable) { $null } else { (Resolve-Path -LiteralPath $Executable).Path }
$resolvedDirectExecutable = (Resolve-Path -LiteralPath $DirectExecutable).Path
$suiteRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("subtitler-smoke-" + [guid]::NewGuid().ToString("N"))
$oldAppData = $env:APPDATA
$oldLocalAppData = $env:LOCALAPPDATA
$oldUserData = $env:SUBUTL_USER_DATA_DIR
$oldElectronRunAsNode = $env:ELECTRON_RUN_AS_NODE

function Get-RelativeStateEntries {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return @("<missing>") }
    $rootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $entries = @(Get-ChildItem -LiteralPath $Root -Force -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        [System.IO.Path]::GetFullPath($_.FullName).Substring($rootPrefix.Length)
    })
    if ($entries.Count -eq 0) { return @("<empty>") }
    return $entries
}

function Get-ProcessSnapshot {
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId, ParentProcessId, CreationDate, ExecutablePath, Name)
}

function Add-ScenarioProcesses {
    param(
        [hashtable]$Tracked,
        [int]$RootProcessId,
        [datetime]$StartedAt
    )

    $snapshot = @(Get-ProcessSnapshot)
    $root = $snapshot | Where-Object { [int]$_.ProcessId -eq $RootProcessId } | Select-Object -First 1
    if ($root) {
        $Tracked[$RootProcessId] = [pscustomobject]@{
            CreationDate = $root.CreationDate
            ExecutablePath = $root.ExecutablePath
            Name = $root.Name
        }
    }
    elseif (-not $Tracked.ContainsKey($RootProcessId)) {
        # Keep the launcher PID as an ancestry anchor even when a short-lived
        # NSIS wrapper exits before the first process snapshot completes.
        $Tracked[$RootProcessId] = [pscustomobject]@{
            CreationDate = $null
            ExecutablePath = $resolvedPortableExecutable
            Name = [System.IO.Path]::GetFileName($resolvedPortableExecutable)
        }
    }

    do {
        $added = $false
        foreach ($process in $snapshot) {
            $processId = [int]$process.ProcessId
            $parentId = [int]$process.ParentProcessId
            if ($Tracked.ContainsKey($processId) -or -not $Tracked.ContainsKey($parentId)) { continue }
            if ($process.CreationDate -and $process.CreationDate -lt $StartedAt.AddSeconds(-2)) { continue }
            $Tracked[$processId] = [pscustomobject]@{
                CreationDate = $process.CreationDate
                ExecutablePath = $process.ExecutablePath
                Name = $process.Name
            }
            $added = $true
        }
    } while ($added)

    return $snapshot
}

function Get-RunningScenarioProcesses {
    param([hashtable]$Tracked, [object[]]$Snapshot = $(Get-ProcessSnapshot))

    @($Snapshot | Where-Object {
        $processId = [int]$_.ProcessId
        if (-not $Tracked.ContainsKey($processId)) { return $false }
        $identity = $Tracked[$processId]
        # Matching creation times prevents an exited scenario PID from later
        # referring to an unrelated process if Windows reuses that PID.
        -not $identity.CreationDate -or $_.CreationDate -eq $identity.CreationDate
    })
}

function Get-ScenarioWindowProcess {
    param([hashtable]$Tracked, [object[]]$Snapshot)

    foreach ($process in (Get-RunningScenarioProcesses $Tracked $Snapshot)) {
        $windowProcess = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        if ($windowProcess -and $windowProcess.MainWindowHandle -ne 0) { return $windowProcess }
    }
    return $null
}

function Stop-ScenarioProcesses {
    param(
        [string]$Scenario,
        [hashtable]$Tracked,
        [int]$RootProcessId,
        [datetime]$StartedAt,
        [System.Diagnostics.Process]$WindowProcess
    )

    if ($WindowProcess -and -not $WindowProcess.HasExited) {
        $null = $WindowProcess.CloseMainWindow()
        $null = $WindowProcess.WaitForExit(10000)
    }

    # Refresh ancestry before and after termination so Electron helpers spawned
    # during shutdown are included. Only descendants of this scenario's exact
    # launcher are ever eligible for termination.
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        $snapshot = Add-ScenarioProcesses $Tracked $RootProcessId $StartedAt
        $running = @(Get-RunningScenarioProcesses $Tracked $snapshot)
        if ($running.Count -eq 0) { break }
        $running | Sort-Object ProcessId -Descending | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 250
    }

    Start-Sleep -Milliseconds 250
    $finalSnapshot = Add-ScenarioProcesses $Tracked $RootProcessId $StartedAt
    $remaining = @(Get-RunningScenarioProcesses $Tracked $finalSnapshot)
    if ($remaining.Count -ne 0) {
        $details = ($remaining | ForEach-Object {
            $path = if ($_.ExecutablePath) { $_.ExecutablePath } else { "<path unavailable>" }
            "PID $($_.ProcessId) ($($_.Name), $path)"
        }) -join "; "
        throw "${Scenario}: scenario processes remained after cleanup: $details"
    }
}

function Invoke-IsolatedLaunch {
    param([string]$Scenario, [scriptblock]$Arrange, [scriptblock]$Assert)

    $startedAt = Get-Date
    $profileRoot = Join-Path $suiteRoot $Scenario
    $appData = Join-Path $profileRoot "Roaming"
    $localAppData = Join-Path $profileRoot "Local"
    $userData = Join-Path $appData "SubUtl"
    New-Item -ItemType Directory -Path $appData, $localAppData | Out-Null
    if ($Arrange) { & $Arrange $appData $userData }

    $env:APPDATA = $appData
    $env:LOCALAPPDATA = $localAppData
    $env:SUBUTL_USER_DATA_DIR = $userData
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $overrideArgument = '--subutl-user-data-dir="{0}"' -f $userData
    # The NSIS portable wrapper does not reliably forward custom environment
    # variables or arguments to its extracted child. State scenarios therefore
    # run against the equivalent direct packaged executable.
    $launched = Start-Process -FilePath $resolvedDirectExecutable -ArgumentList $overrideArgument -PassThru
    $tracked = @{}
    $windowProcess = $null
    try {
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            $snapshot = Add-ScenarioProcesses $tracked $launched.Id $startedAt
            $windowProcess = Get-ScenarioWindowProcess $tracked $snapshot
            if ($windowProcess) { break }
            if ($launched.HasExited -and $launched.ExitCode -ne 0) {
                throw "$Scenario launcher exited with code $($launched.ExitCode) before opening a window."
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $windowProcess) { throw "${Scenario}: no SubUtl window appeared within $TimeoutSeconds seconds." }
        if ($Assert) {
            $assertionError = $null
            while ((Get-Date) -lt $deadline) {
                try {
                    & $Assert $appData $userData
                    $assertionError = $null
                    break
                }
                catch {
                    $assertionError = $_
                    Start-Sleep -Milliseconds 100
                }
            }
            if ($assertionError) {
                $legacyRoot = Join-Path $appData "subtitler-frontend"
                $userEntries = (Get-RelativeStateEntries $userData) -join ", "
                $legacyEntries = (Get-RelativeStateEntries $legacyRoot) -join ", "
                throw "${Scenario}: state initialization did not complete within $TimeoutSeconds seconds. $($assertionError.Exception.Message) Isolated userData entries: [$userEntries]. Legacy entries: [$legacyEntries]."
            }
        }
        [pscustomobject]@{ Scenario = $Scenario; Status = "ready"; ProcessId = $windowProcess.Id; TrackedProcesses = $tracked.Count; IsolatedUserData = $userData }
    }
    finally {
        Stop-ScenarioProcesses $Scenario $tracked $launched.Id $startedAt $windowProcess
    }
}

function Invoke-PortableWrapperLaunch {
    $startedAt = Get-Date
    Write-Warning "Portable wrapper smoke uses the normal SubUtl profile because NSIS does not forward the isolation override. It only verifies window launch and clean close."
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $launched = Start-Process -FilePath $resolvedPortableExecutable -PassThru
    $tracked = @{}
    $windowProcess = $null
    try {
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            $snapshot = Add-ScenarioProcesses $tracked $launched.Id $startedAt
            $windowProcess = Get-ScenarioWindowProcess $tracked $snapshot
            if ($windowProcess) { break }
            if ($launched.HasExited -and $launched.ExitCode -ne 0) {
                throw "Portable wrapper exited with code $($launched.ExitCode) before opening a window."
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $windowProcess) { throw "Portable wrapper: no SubUtl window appeared within $TimeoutSeconds seconds." }
        [pscustomobject]@{ Scenario = "portable-wrapper"; Status = "ready"; ProcessId = $windowProcess.Id; TrackedProcesses = $tracked.Count; IsolatedUserData = "No (normal profile launch only)" }
    }
    finally {
        Stop-ScenarioProcesses "portable-wrapper" $tracked $launched.Id $startedAt $windowProcess
    }
}

try {
    $results = @()
    # This is intentionally separate from the isolated state suite below. The
    # wrapper launch may perform the same benign state reads/writes as a normal
    # user launch, but no migration or corruption fixtures are placed there.
    if (-not $SkipPortable) {
        $results += Invoke-PortableWrapperLaunch
    }
    $results += Invoke-IsolatedLaunch "clean" {} {
        param($appData, $userData)
        if (-not (Test-Path -LiteralPath (Join-Path $userData "settings.json"))) { throw "Clean launch did not initialize isolated state." }
    }
    $results += Invoke-IsolatedLaunch "legacy-migration" {
        param($appData, $userData)
        $legacy = Join-Path $appData "subtitler-frontend"
        New-Item -ItemType Directory -Path $legacy | Out-Null
        '{"theme":"forest"}' | Set-Content -LiteralPath (Join-Path $legacy "settings.json") -Encoding utf8
    } {
        param($appData, $userData)
        if (-not (Test-Path -LiteralPath (Join-Path $userData "settings.json"))) { throw "Legacy state was not migrated to isolated user data." }
        if (Test-Path -LiteralPath (Join-Path $appData "subtitler-frontend")) { throw "Legacy state root still exists after migration." }
    }
    $results += Invoke-IsolatedLaunch "corrupt-recovery" {
        param($appData, $userData)
        New-Item -ItemType Directory -Path $userData | Out-Null
        '{broken' | Set-Content -LiteralPath (Join-Path $userData "settings.json") -Encoding utf8
        '{"theme":"forest"}' | Set-Content -LiteralPath (Join-Path $userData "settings.json.bak") -Encoding utf8
    } {
        param($appData, $userData)
        $settings = Get-Content -LiteralPath (Join-Path $userData "settings.json") -Raw | ConvertFrom-Json
        if ($settings.theme -ne "forest") { throw "Corrupt primary state was not restored from its backup." }
    }
    $results | Format-Table -AutoSize
}
finally {
    $env:APPDATA = $oldAppData
    $env:LOCALAPPDATA = $oldLocalAppData
    if ($null -eq $oldUserData) { Remove-Item Env:SUBUTL_USER_DATA_DIR -ErrorAction SilentlyContinue } else { $env:SUBUTL_USER_DATA_DIR = $oldUserData }
    if ($null -eq $oldElectronRunAsNode) { Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue } else { $env:ELECTRON_RUN_AS_NODE = $oldElectronRunAsNode }
    Remove-Item -LiteralPath $suiteRoot -Recurse -Force -ErrorAction SilentlyContinue
}
