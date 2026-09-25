<#
.SYNOPSIS
    Builds JarvisTray.exe with PyInstaller (--onefile --noconsole).

.DESCRIPTION
    Run from windows_client\. Expects a Python 3.12 venv with requirements-dev.txt installed;
    creates one at .\.build-venv if missing. Output: windows_client\dist\JarvisTray.exe.
#>
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$venvDir = Join-Path $root '.build-venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating build venv at $venvDir"
    py -3.12 -m venv $venvDir
}

& $venvPython -m pip install --upgrade pip | Out-Null
& $venvPython -m pip install -r (Join-Path $root 'requirements-dev.txt')

Push-Location $root
try {
    & $venvPython -m PyInstaller `
        --onefile `
        --noconsole `
        --name JarvisTray `
        --hidden-import pystray._win32 `
        --hidden-import pynput.keyboard._win32 `
        --hidden-import pynput.mouse._win32 `
        --paths . `
        'run_tray.py'

    if (-not (Test-Path (Join-Path $root 'dist\JarvisTray.exe'))) {
        throw 'Build finished but dist\JarvisTray.exe is missing.'
    }
    Write-Host "Built $(Join-Path $root 'dist\JarvisTray.exe')"
}
finally {
    Pop-Location
}
