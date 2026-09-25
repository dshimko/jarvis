<#
.SYNOPSIS
    Installs the Jarvis Windows tray client: whisper.cpp, optional Piper, JarvisTray.exe, the
    logon Scheduled Task, and (optionally) the WSL2 idle-timeout override.

.DESCRIPTION
    Run from an ordinary PowerShell 5.1+ prompt inside windows_client\. Re-running is safe: every
    step skips work that is already done, and the Scheduled Task is unregistered/re-registered.
#>
$ErrorActionPreference = 'Stop'
$env:WSL_UTF8 = '1'   # wsl.exe otherwise emits UTF-16 for some subcommands

$JarvisDir = Join-Path $env:LOCALAPPDATA 'Jarvis'
$WslDistro = 'Ubuntu'
New-Item -ItemType Directory -Path $JarvisDir -Force | Out-Null

function Invoke-WslUtf8 {
    <#
    M5: `2>&1` merges wsl.exe's stderr into the output stream as error records, which under the
    script-wide $ErrorActionPreference='Stop' would turn a normal warning-to-stderr into a
    terminating error and abort the whole install. Locally relax it to 'Continue' (function-scoped
    -- it does not leak to the caller) and report failure via ExitCode/$LASTEXITCODE instead.
    #>
    param([string[]]$WslArgs)
    $ErrorActionPreference = 'Continue'
    try {
        $output = & wsl.exe @WslArgs 2>&1
        $exitCode = $LASTEXITCODE
    } catch {
        $output = $_.Exception.Message
        $exitCode = 1
    }
    return [pscustomobject]@{
        Output   = (($output -join "`n") -replace "`r", '')
        ExitCode = $exitCode
    }
}

function Set-Utf8NoBom {
    <#
    LOW: Windows PowerShell 5.1's `-Encoding utf8` always writes a BOM, which trips up tools that
    expect plain UTF-8 (.wslconfig is an ini-style file; client.yaml is read by PyYAML, which is
    lenient but doesn't need it either). Write both without a BOM explicitly.
    #>
    param([string]$Path, [string]$Content)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

# ---------------------------------------------------------------------------
# 1. whisper.cpp
# ---------------------------------------------------------------------------
function Install-Whisper {
    $whisperDir = Join-Path $JarvisDir 'whisper'
    New-Item -ItemType Directory -Path $whisperDir -Force | Out-Null

    $exe = Get-ChildItem -Path $whisperDir -Filter 'whisper-cli.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $exe) {
        $zipPath = Join-Path $whisperDir 'whisper-bin-x64.zip'
        Write-Host 'Downloading whisper.cpp...'
        Invoke-WebRequest -Uri 'https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-bin-x64.zip' -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $whisperDir -Force
        Remove-Item $zipPath
        $exe = Get-ChildItem -Path $whisperDir -Filter 'whisper-cli.exe' -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $exe) { throw 'whisper-cli.exe not found after extracting whisper.cpp.' }
    } else {
        Write-Host 'whisper-cli.exe already present, skipping download.'
    }

    $modelPath = Join-Path $whisperDir 'ggml-base.en.bin'
    if (-not (Test-Path $modelPath)) {
        Write-Host 'Downloading ggml-base.en.bin...'
        Invoke-WebRequest -Uri 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin' -OutFile $modelPath
    } else {
        Write-Host 'ggml-base.en.bin already present, skipping download.'
    }

    return @{ Exe = $exe.FullName; Model = $modelPath }
}

# ---------------------------------------------------------------------------
# 2. Piper (optional)
# ---------------------------------------------------------------------------
function Install-Piper {
    $answer = Read-Host 'Install Piper for higher-quality local TTS? (y/N)'
    if ($answer -notmatch '^[Yy]') {
        Write-Host 'Skipping Piper (pyttsx3/SAPI will be used instead).'
        return @{ Exe = $null; Voice = $null }
    }

    $piperDir = Join-Path $JarvisDir 'piper'
    New-Item -ItemType Directory -Path $piperDir -Force | Out-Null

    $exe = Join-Path $piperDir 'piper.exe'
    if (-not (Test-Path $exe)) {
        $zipPath = Join-Path $piperDir 'piper_windows_amd64.zip'
        Write-Host 'Downloading Piper...'
        Invoke-WebRequest -Uri 'https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip' -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $piperDir -Force
        Remove-Item $zipPath
        if (-not (Test-Path $exe)) {
            $found = Get-ChildItem -Path $piperDir -Filter 'piper.exe' -Recurse | Select-Object -First 1
            if ($found) { $exe = $found.FullName } else { throw 'piper.exe not found after extracting Piper.' }
        }
    } else {
        Write-Host 'piper.exe already present, skipping download.'
    }

    $voiceBase = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium'
    $onnx = Join-Path $piperDir 'en_US-ryan-medium.onnx'
    if (-not (Test-Path $onnx)) {
        Write-Host 'Downloading en_US-ryan-medium voice...'
        Invoke-WebRequest -Uri "$voiceBase/en_US-ryan-medium.onnx" -OutFile $onnx
    }
    if (-not (Test-Path "$onnx.json")) {
        Invoke-WebRequest -Uri "$voiceBase/en_US-ryan-medium.onnx.json" -OutFile "$onnx.json"
    }

    return @{ Exe = $exe; Voice = $onnx }
}

# ---------------------------------------------------------------------------
# 3. Build / install JarvisTray.exe
# ---------------------------------------------------------------------------
function Install-TrayExe {
    $root = $PSScriptRoot
    $target = Join-Path $JarvisDir 'JarvisTray.exe'
    $hasPy312 = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { $hasPy312 = ((& py -3.12 -c 'print(1)' 2>$null) -eq '1') } catch { $hasPy312 = $false }
    }

    $built = Join-Path $root 'dist\JarvisTray.exe'
    if ($hasPy312) {
        Write-Host 'Python 3.12 found, building JarvisTray.exe...'
        & (Join-Path $root 'build.ps1')
    } elseif (Test-Path $built) {
        Write-Host 'Python 3.12 not found; using the prebuilt dist\JarvisTray.exe.'
    } else {
        throw ("Python 3.12 was not found (py -3.12) and windows_client\dist\JarvisTray.exe does " +
               "not exist. Install Python 3.12 (https://www.python.org/downloads/) and re-run, " +
               "or build once on another machine and copy dist\JarvisTray.exe here.")
    }

    if (-not (Test-Path $built)) { throw "Build did not produce $built." }
    Copy-Item -Path $built -Destination $target -Force
    Write-Host "Installed $target"
}

# ---------------------------------------------------------------------------
# 4. client.yaml (only if absent)
# ---------------------------------------------------------------------------
function Format-YamlScalar {
    param([string]$Value)
    if ([string]::IsNullOrEmpty($Value)) { return 'null' }
    return "'$($Value -replace "'", "''")'"
}

function Write-ClientConfig {
    param($Whisper, $Piper)

    $configPath = Join-Path $JarvisDir 'client.yaml'
    if (Test-Path $configPath) {
        Write-Host 'client.yaml already exists, leaving it alone.'
        return
    }

    $lines = @(
        'api_url: http://localhost:8765'
        "token_path: $(Format-YamlScalar (Join-Path $JarvisDir 'api_token'))"
        "whisper_exe: $(Format-YamlScalar $Whisper.Exe)"
        "whisper_model: $(Format-YamlScalar $Whisper.Model)"
        "piper_exe: $(Format-YamlScalar $Piper.Exe)"
        "piper_voice: $(Format-YamlScalar $Piper.Voice)"
        "hotkey_work: '<ctrl>+<alt>+w'"
        "hotkey_personal: '<ctrl>+<alt>+p'"
        "wsl_distro: $WslDistro"
        'vault_work: Jarvis-Work'
        'vault_personal: Jarvis-Personal'
        'sample_rate: 16000'
    )
    Set-Utf8NoBom -Path $configPath -Content (($lines -join "`r`n") + "`r`n")
    Write-Host "Wrote $configPath"
}

# ---------------------------------------------------------------------------
# 5. Logon Scheduled Task (re-runnable)
# ---------------------------------------------------------------------------
function Install-StartupTask {
    $taskName = 'Jarvis'
    $startScript = Join-Path $JarvisDir 'start-jarvis.ps1'
    $trayExe = Join-Path $JarvisDir 'JarvisTray.exe'

    @"
`$env:WSL_UTF8 = '1'
wsl.exe -d $WslDistro -e sh -lc 'export XDG_RUNTIME_DIR=/run/user/`$(id -u); systemctl --user start jarvis'
Start-Process -FilePath '$trayExe' -WindowStyle Hidden
"@ | Set-Content -Path $startScript -Encoding utf8

    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startScript`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal | Out-Null

    Write-Host "Registered Scheduled Task '$taskName' (runs at logon)."
}

# ---------------------------------------------------------------------------
# 6. .wslconfig vmIdleTimeout (optional, version/build gated)
# ---------------------------------------------------------------------------
function Test-WslIdleTimeoutSupported {
    $result = Invoke-WslUtf8 @('--version')
    if ($result.ExitCode -ne 0 -or -not $result.Output) { return $false }

    $match = [regex]::Match($result.Output, 'WSL version:\s*([\d.]+)')
    if (-not $match.Success) { return $false }
    if ([version]$match.Groups[1].Value -lt [version]'2.0.0') { return $false }

    return ([System.Environment]::OSVersion.Version.Build -ge 22000)
}

function Set-WslIdleTimeout {
    if (-not (Test-WslIdleTimeoutSupported)) {
        Write-Host 'Skipping .wslconfig: needs Store WSL >= 2.0.0 on Windows 11 (build >= 22000).'
        return
    }
    $answer = Read-Host 'Set vmIdleTimeout=-1 in .wslconfig so the WSL VM never idles out? (y/N)'
    if ($answer -notmatch '^[Yy]') {
        Write-Host 'Skipping .wslconfig.'
        return
    }

    $wslConfigPath = Join-Path $env:USERPROFILE '.wslconfig'
    # LOW: Get-Content -Raw returns $null (not '') for a 0-byte file, and calling .TrimEnd() on
    # $null throws under PS 5.1 -- coalesce explicitly.
    $content = ''
    if (Test-Path $wslConfigPath) {
        $raw = Get-Content -Path $wslConfigPath -Raw
        if ($raw) { $content = $raw }
    }

    if ($content -notmatch '(?m)^\s*\[wsl2\]\s*$') {
        $content = $content.TrimEnd() + "`r`n`r`n[wsl2]`r`nvmIdleTimeout=-1`r`n"
    } elseif ($content -match '(?m)^\s*vmIdleTimeout\s*=.*$') {
        $content = $content -replace '(?m)^\s*vmIdleTimeout\s*=.*$', 'vmIdleTimeout=-1'
    } else {
        $content = $content -replace '(?m)^\s*\[wsl2\]\s*$', "[wsl2]`r`nvmIdleTimeout=-1"
    }

    Set-Utf8NoBom -Path $wslConfigPath -Content $content
    Write-Host "Updated $wslConfigPath"
}

# ---------------------------------------------------------------------------
# 7. Final verification
# ---------------------------------------------------------------------------
function Test-DaemonInstall {
    # M5: verification is best-effort and must never abort the (already-finished) install.
    try {
        Write-Host "Verifying the daemon in WSL distro '$WslDistro'..."
        # B11: a bare `wsl -e systemctl --user ...` has no XDG_RUNTIME_DIR, so it can't reach the
        # user bus and fails silently -- use the same login-shell form as start-jarvis.ps1.
        $workDirResult = Invoke-WslUtf8 @('-d', $WslDistro, '-e', 'sh', '-lc',
            'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user show -p WorkingDirectory --value jarvis')
        $workDir = $workDirResult.Output.Trim()
        if ($workDirResult.ExitCode -ne 0 -or -not $workDir) {
            Write-Warning 'Could not read the jarvis unit WorkingDirectory; is the service installed?'
            return
        }
        Write-Host "Service working directory: $workDir"

        $verify = Invoke-WslUtf8 @('-d', $WslDistro, '-e', 'sh', '-lc',
            "cd '$workDir' && .venv/bin/python -m jarvis.verify_secrets")
        Write-Host $verify.Output
        if ($verify.ExitCode -eq 0) { Write-Host 'verify_secrets: PASS' }
        else { Write-Warning 'verify_secrets: FAIL (see output above)' }

        $tokenPath = Join-Path $JarvisDir 'api_token'
        if (Test-Path $tokenPath) {
            $token = (Get-Content -Path $tokenPath -Raw).Trim()
            Write-Host 'Checking /health...'
            & curl.exe -s -H "Authorization: Bearer $token" http://localhost:8765/health
            Write-Host ''
        } else {
            Write-Warning "No api_token at $tokenPath yet; skipping /health check."
        }
    } catch {
        Write-Warning "Verification step failed: $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Host '== Step 1: whisper.cpp =='
$whisper = Install-Whisper

Write-Host "`n== Step 2: Piper (optional) =="
$piper = Install-Piper

Write-Host "`n== Step 3: JarvisTray.exe =="
Install-TrayExe

Write-Host "`n== Step 4: client.yaml =="
Write-ClientConfig -Whisper $whisper -Piper $piper

Write-Host "`n== Step 5: logon Scheduled Task =="
Install-StartupTask

Write-Host "`n== Step 6: .wslconfig =="
Set-WslIdleTimeout

Write-Host "`n== Step 7: verification =="
Test-DaemonInstall

Write-Host "`nInstall complete. Log off/on, or run 'Start-ScheduledTask -TaskName Jarvis', to start the tray."
