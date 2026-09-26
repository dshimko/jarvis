<#
.SYNOPSIS
    Installs the Jarvis Windows tray client: whisper.cpp, optional Piper, JarvisTray.exe, the
    logon Scheduled Task, and -- profile dependent -- either Tailscale + Syncthing (aws) or the
    WSL2 idle-timeout override (wsl).

.DESCRIPTION
    Run from an ordinary PowerShell 5.1+ prompt inside windows_client\. Re-running is safe: every
    step skips work that is already done, and the Scheduled Task is unregistered/re-registered.

.PARAMETER ClientProfile
    'aws' (default, PLAN.md AD16): the client talks to the two per-mode daemons on the EC2
    instance over Tailscale (http://jarvis:8781 work, :8782 personal), fetching API tokens from
    Secrets Manager at request time (never written to disk). Installs the AWS CLI, Tailscale, and
    Syncthing locally and registers each vault folder with its own mode's Syncthing device.
    'wsl': the legacy local-WSL2 path (single api_url/token_path, daemon started via wsl.exe).
    (M6: named ClientProfile, not Profile -- the latter shadows PowerShell's own automatic
    $PROFILE variable.)

.PARAMETER SyncthingWorkDeviceId
    The WORK Syncthing instance's own device id on the box (`syncthing cli show system` run as
    jarvis-work, or its GUI at :8384 over an SSM port-forward), used to register the Jarvis-Work
    vault folder in the workstation's local Syncthing over its REST API. Validated as a Syncthing
    device id (7 base32 groups of 7, hyphen-separated) if given; omit to skip that folder's
    registration with a warning, re-runnable later.

.PARAMETER SyncthingPersonalDeviceId
    The same, for the PERSONAL Syncthing instance (jarvis-personal, GUI at :8385) and the
    Jarvis-Personal vault folder. H5: work and personal are separate Syncthing instances with
    separate device ids -- one shared id would connect the wrong device to a folder.

.PARAMETER AwsProfile
    The IAM Identity Center profile name (the JarvisClient permission set, PLAN.md AD25) used for
    `aws secretsmanager get-secret-value` and written into client.yaml as aws_profile. Only used
    under -ClientProfile aws.

.PARAMETER AwsRegion
    The region passed as `--region` on every `aws secretsmanager get-secret-value` call and
    written into client.yaml as aws_region (PLAN.md AD34: region literals leave every script,
    including this one). Only used under -ClientProfile aws.
#>
param(
    [ValidateSet('aws', 'wsl')]
    [string]$ClientProfile = 'aws',
    [string]$SyncthingWorkDeviceId = '',
    [string]$SyncthingPersonalDeviceId = '',
    [string]$AwsProfile = 'jarvis-client',
    [string]$AwsRegion = 'us-east-1'
)
$ErrorActionPreference = 'Stop'
$env:WSL_UTF8 = '1'   # wsl.exe otherwise emits UTF-16 for some subcommands

$JarvisDir = Join-Path $env:LOCALAPPDATA 'Jarvis'
$WslDistro = 'Ubuntu'
New-Item -ItemType Directory -Path $JarvisDir -Force | Out-Null

# H5: fail fast on a malformed device id rather than silently registering a bogus/empty one. Done
# here (not a [ValidatePattern] attribute) so the default '' -- "not given" -- stays legal.
$SyncthingDeviceIdPattern = '^[A-Z2-7]{7}(-[A-Z2-7]{7}){7}$'
function Test-SyncthingDeviceIdParam {
    param([string]$ParamName, [string]$Value)
    if ($Value -and $Value -notmatch $script:SyncthingDeviceIdPattern) {
        throw "-$ParamName '$Value' does not look like a Syncthing device id (7 groups of 7, e.g. ABCDEFG-HIJKLMN-...-XXXXXXX)."
    }
}
Test-SyncthingDeviceIdParam -ParamName 'SyncthingWorkDeviceId' -Value $SyncthingWorkDeviceId
Test-SyncthingDeviceIdParam -ParamName 'SyncthingPersonalDeviceId' -Value $SyncthingPersonalDeviceId

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
# 4. Tailscale + Syncthing (aws profile only; PLAN.md AD16, brief 4.6)
# ---------------------------------------------------------------------------
function Install-WingetOrFallback {
    <#
    Tries `winget install <WingetId>` first (silent, pre-accepted agreements); on any failure, or
    when winget itself isn't present (older Windows 10 images), downloads and runs the vendor's
    own installer instead. Skips entirely if the tool is already on PATH.
    #>
    param(
        [string]$WingetId,
        [string]$CommandName,
        [string]$FallbackUrl,
        [string[]]$FallbackArgs
    )
    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        Write-Host "$CommandName already installed, skipping."
        return
    }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing $CommandName via winget ($WingetId)..."
        $result = Start-Process -FilePath winget -ArgumentList @(
            'install', '--id', $WingetId, '--silent', '--accept-package-agreements', '--accept-source-agreements'
        ) -Wait -PassThru -NoNewWindow
        if ($result.ExitCode -eq 0) {
            Write-Host "$CommandName installed via winget."
            return
        }
        Write-Warning "winget install of $WingetId exited $($result.ExitCode); falling back to the official installer."
    } else {
        Write-Host "winget not found; installing $CommandName from the official installer."
    }
    $installerPath = Join-Path $env:TEMP "$CommandName-installer.exe"
    Invoke-WebRequest -Uri $FallbackUrl -OutFile $installerPath
    Start-Process -FilePath $installerPath -ArgumentList $FallbackArgs -Wait
    Remove-Item $installerPath -ErrorAction SilentlyContinue
}

function Install-Tailscale {
    Install-WingetOrFallback -WingetId 'tailscale.tailscale' -CommandName 'tailscale' `
        -FallbackUrl 'https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe' `
        -FallbackArgs @('/quiet')
}

function Sync-EnvPathFromMachine {
    # M6: winget and MSI installers update the persisted machine/user PATH, but this
    # already-running PowerShell process's $env:Path was captured at launch and never sees that
    # change on its own -- newly installed commands (aws, tailscale, syncthing) would otherwise
    # stay unresolvable until a new shell, later in this very script.
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path', 'User')
}

function Install-AwsCli {
    if (Get-Command aws -ErrorAction SilentlyContinue) {
        Write-Host 'aws CLI already installed, skipping.'
        return
    }
    $installedViaWinget = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'Installing AWS CLI v2 via winget...'
        $result = Start-Process -FilePath winget -ArgumentList @(
            'install', '--id', 'Amazon.AWSCLI', '--silent', '--accept-package-agreements', '--accept-source-agreements'
        ) -Wait -PassThru -NoNewWindow
        if ($result.ExitCode -eq 0) { $installedViaWinget = $true }
        else { Write-Warning "winget install of Amazon.AWSCLI exited $($result.ExitCode); falling back to the official MSI." }
    } else {
        Write-Host 'winget not found; installing AWS CLI v2 from the official MSI.'
    }
    if (-not $installedViaWinget) {
        $msiPath = Join-Path $env:TEMP 'AWSCLIV2.msi'
        Invoke-WebRequest -Uri 'https://awscli.amazonaws.com/AWSCLIV2.msi' -OutFile $msiPath
        Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', "`"$msiPath`"", '/qn') -Wait
        Remove-Item $msiPath -ErrorAction SilentlyContinue
    }
    Sync-EnvPathFromMachine
    if (Get-Command aws -ErrorAction SilentlyContinue) {
        Write-Host 'aws CLI installed.'
    } else {
        Write-Warning 'aws CLI install finished but "aws" is still not resolvable in this session; open a new PowerShell window.'
    }
}

function Install-Syncthing {
    if (Get-Command syncthing -ErrorAction SilentlyContinue) {
        Write-Host 'syncthing already installed, skipping.'
        return
    }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'Installing Syncthing via winget...'
        $result = Start-Process -FilePath winget -ArgumentList @(
            'install', '--id', 'Syncthing.Syncthing', '--silent', '--accept-package-agreements', '--accept-source-agreements'
        ) -Wait -PassThru -NoNewWindow
        if ($result.ExitCode -eq 0) {
            Write-Host 'Syncthing installed via winget.'
            return
        }
        Write-Warning "winget install of Syncthing exited $($result.ExitCode); falling back to the official zip."
    } else {
        Write-Host 'winget not found; installing Syncthing from the official zip.'
    }
    $syncthingDir = Join-Path $JarvisDir 'syncthing'
    New-Item -ItemType Directory -Path $syncthingDir -Force | Out-Null
    $zipPath = Join-Path $syncthingDir 'syncthing.zip'
    Invoke-WebRequest -Uri 'https://github.com/syncthing/syncthing/releases/latest/download/syncthing-windows-amd64.zip' -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $syncthingDir -Force
    Remove-Item $zipPath
}

function Get-SyncthingExe {
    $cmd = Get-Command syncthing -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $found = Get-ChildItem -Path (Join-Path $JarvisDir 'syncthing') -Filter 'syncthing.exe' `
        -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

function Start-SyncthingIfNeeded {
    if (Get-Process -Name syncthing -ErrorAction SilentlyContinue) { return }
    $exe = Get-SyncthingExe
    if (-not $exe) { throw 'syncthing.exe not found after install.' }
    Write-Host 'Starting Syncthing (background, no browser)...'
    Start-Process -FilePath $exe -ArgumentList @('serve', '--no-browser') -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

function Get-SyncthingApiKey {
    # Default per-user config location on Windows; DESIGN.md 10.3 references the same directory
    # for the device key (%LOCALAPPDATA%\Syncthing\key.pem).
    $configPath = Join-Path $env:LOCALAPPDATA 'Syncthing\config.xml'
    if (-not (Test-Path $configPath)) { return $null }
    [xml]$xml = Get-Content -Path $configPath -Raw
    return $xml.configuration.gui.apikey
}

function Wait-SyncthingReady {
    param([string]$ApiKey, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri 'http://127.0.0.1:8384/rest/system/ping' `
                -Headers @{ 'X-API-Key' = $ApiKey } -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

function Register-ServerDevice {
    # H5: introducer=false and autoAcceptFolders=false on the workstation's OWN record of the
    # server device too (symmetric with the server's own settings for the workstation device,
    # DESIGN.md AD8 / PLAN.md "Small items") -- neither side auto-trusts anything from the other.
    param([string]$ApiKey, [string]$DeviceId, [string]$Address)
    $body = @{
        deviceID          = $DeviceId
        name              = 'jarvis-server'
        addresses         = @($Address)
        introducer        = $false
        autoAcceptFolders = $false
    } | ConvertTo-Json -Depth 6
    Invoke-RestMethod -Uri "http://127.0.0.1:8384/rest/config/devices/$DeviceId" -Method Put `
        -Headers @{ 'X-API-Key' = $ApiKey } -ContentType 'application/json' -Body $body | Out-Null
}

function Register-VaultFolder {
    param([string]$ApiKey, [string]$FolderId, [string]$Path, [string]$DeviceId)
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    $body = @{
        id      = $FolderId
        label   = $FolderId
        path    = $Path
        type    = 'sendreceive'
        devices = @(@{ deviceID = $DeviceId })
    } | ConvertTo-Json -Depth 6
    Invoke-RestMethod -Uri "http://127.0.0.1:8384/rest/config/folders/$FolderId" -Method Put `
        -Headers @{ 'X-API-Key' = $ApiKey } -ContentType 'application/json' -Body $body | Out-Null
}

function Register-VaultFolders {
    # H5: work and personal are separate Syncthing instances with separate device ids and ports
    # (DESIGN.md AD8: tcp/quic 22000 for work, 22001 for personal) -- Jarvis-Work is registered
    # with the WORK device only, Jarvis-Personal with the PERSONAL device only. Either id may be
    # omitted independently.
    param([string]$WorkDeviceId, [string]$PersonalDeviceId)
    if (-not $WorkDeviceId -and -not $PersonalDeviceId) {
        Write-Warning ("No -SyncthingWorkDeviceId or -SyncthingPersonalDeviceId given; skipping Syncthing " +
            "folder registration entirely. Get each from the box ('syncthing cli show system' run as " +
            "jarvis-work / jarvis-personal, or each instance's own GUI) and re-run install.ps1, or add " +
            "the folders by hand at http://127.0.0.1:8384.")
        return
    }
    Start-SyncthingIfNeeded
    $apiKey = Get-SyncthingApiKey
    if (-not $apiKey) {
        Write-Warning 'Could not read the local Syncthing API key; skipping folder registration.'
        return
    }
    if (-not (Wait-SyncthingReady -ApiKey $apiKey)) {
        Write-Warning 'Syncthing did not respond in time; skipping folder registration.'
        return
    }

    if ($WorkDeviceId) {
        Register-ServerDevice -ApiKey $apiKey -DeviceId $WorkDeviceId -Address 'tcp://jarvis:22000'
        Register-VaultFolder -ApiKey $apiKey -FolderId 'jarvis-work' `
            -Path (Join-Path $env:USERPROFILE 'Vaults\Jarvis-Work') -DeviceId $WorkDeviceId
        Write-Host "Registered jarvis-work with the work Syncthing device ($WorkDeviceId, tcp://jarvis:22000)."
    } else {
        Write-Warning 'No -SyncthingWorkDeviceId given; skipping the work vault folder.'
    }

    if ($PersonalDeviceId) {
        Register-ServerDevice -ApiKey $apiKey -DeviceId $PersonalDeviceId -Address 'tcp://jarvis:22001'
        Register-VaultFolder -ApiKey $apiKey -FolderId 'jarvis-personal' `
            -Path (Join-Path $env:USERPROFILE 'Vaults\Jarvis-Personal') -DeviceId $PersonalDeviceId
        Write-Host "Registered jarvis-personal with the personal Syncthing device ($PersonalDeviceId, tcp://jarvis:22001)."
    } else {
        Write-Warning 'No -SyncthingPersonalDeviceId given; skipping the personal vault folder.'
    }

    # H5: the workstation cannot add itself to the server's side of either instance from here --
    # that add happens on the box and is a runbook precondition, not something this script does.
    Write-Host ("Next (runbook step, done on the server side): add this workstation's own Syncthing " +
        "device id to each instance (introducer=false, autoAcceptFolders=false there too) before " +
        "syncing starts.")
}

function Install-SyncthingStartupTask {
    # M7: the portable Syncthing download has no service/auto-start of its own (unlike
    # Tailscale's installer, which registers a Windows service) -- without this, Syncthing only
    # ever runs because Register-VaultFolders happened to start it once during install.
    $taskName = 'JarvisSyncthing'
    $exe = Get-SyncthingExe
    if (-not $exe) {
        Write-Warning 'syncthing.exe not found; skipping its logon Scheduled Task.'
        return
    }
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    $action = New-ScheduledTaskAction -Execute $exe -Argument 'serve --no-browser --no-restart'
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal | Out-Null
    Write-Host "Registered Scheduled Task '$taskName' (starts Syncthing at logon)."
}

# ---------------------------------------------------------------------------
# 5. client.yaml (only if absent)
# ---------------------------------------------------------------------------
function Format-YamlScalar {
    param([string]$Value)
    if ([string]::IsNullOrEmpty($Value)) { return 'null' }
    return "'$($Value -replace "'", "''")'"
}

function Write-ClientConfig {
    param($Whisper, $Piper, [string]$ClientProfile, [string]$AwsProfile, [string]$AwsRegion)

    $configPath = Join-Path $JarvisDir 'client.yaml'
    if (Test-Path $configPath) {
        Write-Host 'client.yaml already exists, leaving it alone.'
        return
    }

    $lines = @("profile: $ClientProfile")
    if ($ClientProfile -eq 'aws') {
        $lines += @(
            'api_url_work: http://jarvis:8781'
            'api_url_personal: http://jarvis:8782'
            'token_source: secretsmanager'
            "aws_profile: $(Format-YamlScalar $AwsProfile)"
            "aws_region: $AwsRegion"
            'token_secret_work: jarvis/work/api-token'
            'token_secret_personal: jarvis/personal/api-token'
        )
    } else {
        $lines += @(
            'api_url: http://localhost:8765'
            "token_path: $(Format-YamlScalar (Join-Path $JarvisDir 'api_token'))"
        )
    }
    $lines += @(
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
    Write-Host "Wrote $configPath (profile: $ClientProfile)"
}

# ---------------------------------------------------------------------------
# 6. Logon Scheduled Task (re-runnable)
# ---------------------------------------------------------------------------
function Install-StartupTask {
    param([string]$ClientProfile)
    $taskName = 'Jarvis'
    $startScript = Join-Path $JarvisDir 'start-jarvis.ps1'
    $trayExe = Join-Path $JarvisDir 'JarvisTray.exe'

    if ($ClientProfile -eq 'wsl') {
        @"
`$env:WSL_UTF8 = '1'
wsl.exe -d $WslDistro -e sh -lc 'export XDG_RUNTIME_DIR=/run/user/`$(id -u); systemctl --user start jarvis'
Start-Process -FilePath '$trayExe' -WindowStyle Hidden
"@ | Set-Content -Path $startScript -Encoding utf8
    } else {
        # aws profile: the daemons run on the EC2 instance, not locally -- nothing to start here
        # but the tray itself. M7: Tailscale's installer registers its own Windows service, but
        # Syncthing's portable download does not -- Install-SyncthingStartupTask (a separate
        # Scheduled Task, JarvisSyncthing) covers that instead.
        @"
Start-Process -FilePath '$trayExe' -WindowStyle Hidden
"@ | Set-Content -Path $startScript -Encoding utf8
    }

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
# 7. .wslconfig vmIdleTimeout (optional, version/build gated; wsl profile only)
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
# 8. Final verification (wsl profile only; aws verification is oauth-login.sh, server-side)
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
            & curl.exe -s -H "Authorization: Bearer $token" http://localhost:8781/health
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
Write-Host "Profile: $ClientProfile"

Write-Host "`n== Step 1: whisper.cpp =="
$whisper = Install-Whisper

Write-Host "`n== Step 2: Piper (optional) =="
$piper = Install-Piper

Write-Host "`n== Step 3: JarvisTray.exe =="
Install-TrayExe

Write-Host "`n== Step 4: client.yaml =="
Write-ClientConfig -Whisper $whisper -Piper $piper -ClientProfile $ClientProfile -AwsProfile $AwsProfile -AwsRegion $AwsRegion

Write-Host "`n== Step 5: logon Scheduled Task =="
Install-StartupTask -ClientProfile $ClientProfile

if ($ClientProfile -eq 'aws') {
    Write-Host "`n== Step 6: AWS CLI, Tailscale, Syncthing =="
    Install-AwsCli
    Install-Tailscale
    Install-Syncthing
    Sync-EnvPathFromMachine
    Install-SyncthingStartupTask
    Register-VaultFolders -WorkDeviceId $SyncthingWorkDeviceId -PersonalDeviceId $SyncthingPersonalDeviceId

    Write-Host "`n== Step 7: AWS sign-in =="
    Write-Host ("Run this once to sign in (needed before the tray can fetch API tokens):`n" +
        "    aws configure sso --profile $AwsProfile")
} else {
    Write-Host "`n== Step 6: .wslconfig =="
    Set-WslIdleTimeout

    Write-Host "`n== Step 7: verification =="
    Test-DaemonInstall
}

Write-Host "`nInstall complete. Log off/on, or run 'Start-ScheduledTask -TaskName Jarvis', to start the tray."
