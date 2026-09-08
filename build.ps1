param(
    [string]$Python = 'C:\Users\medaka\anaconda3\envs\stitch\python.exe',
    [switch]$SkipNative,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}
$environmentRoot = Split-Path -Parent $Python
$env:PATH = "$environmentRoot;$environmentRoot\Scripts;$environmentRoot\Library\bin;$env:PATH"
if (-not $SkipNative) {
    & (Join-Path $repo 'native\build.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Native backend build failed ($LASTEXITCODE)" }
}

& $Python -m pip install -e $repo --no-deps --no-build-isolation
if ($LASTEXITCODE -ne 0) { throw "Editable package install failed ($LASTEXITCODE)" }

if (-not $SkipTests) {
    # forward slashes: the rtk proxy strips backslashes from arguments, which
    # otherwise creates a mangled "UsersmedakaAppData..." dir in the repo root
    $env:PYTEST_ADDOPTS = "-q -p no:cacheprovider --basetemp=$($env:TEMP -replace '\\', '/')/rig-studio-pytest-$PID"
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    Push-Location $repo
    try {
        rtk test $Python -m pytest
        if ($LASTEXITCODE -ne 0) { throw "Test suite failed ($LASTEXITCODE)" }
    }
    finally {
        Pop-Location
    }
}

$installedLauncher = Join-Path $environmentRoot 'Scripts\rig-studio.exe'
if (-not (Test-Path -LiteralPath $installedLauncher)) {
    throw "Installed launcher not found: $installedLauncher"
}
$launcher = Join-Path $repo 'RigStudio.exe'
Copy-Item -LiteralPath $installedLauncher -Destination $launcher -Force
Write-Output "Ready: $launcher"
