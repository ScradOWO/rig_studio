param(
    [ValidateSet('Release', 'RelWithDebInfo')]
    [string]$Configuration = 'Release',
    [string]$SpinnakerRoot = 'C:\Program Files\Teledyne\Spinnaker',
    [string]$BuildRoot = ''
)

$ErrorActionPreference = 'Stop'
$nativeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $BuildRoot) {
    $BuildRoot = Join-Path $env:TEMP 'rig_studio_native_build'
}
$nativeOutput = Join-Path $BuildRoot 'out'

cmake -S $nativeRoot -B $BuildRoot -A x64 `
    "-DSPINNAKER_ROOT=$SpinnakerRoot" `
    "-DRIG_STUDIO_NATIVE_OUTPUT=$nativeOutput"
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed ($LASTEXITCODE)" }
cmake --build $BuildRoot --config $Configuration --parallel
if ($LASTEXITCODE -ne 0) { throw "CMake build failed ($LASTEXITCODE)" }

$builtDll = Join-Path $nativeOutput 'rig_studio_spinnaker.dll'
if (-not (Test-Path -LiteralPath $builtDll)) {
    throw "Native backend build completed without producing $builtDll"
}
$dll = Join-Path (Split-Path -Parent $nativeRoot) 'rig_studio\native\bin\rig_studio_spinnaker.dll'
Copy-Item -LiteralPath $builtDll -Destination $dll -Force
Write-Output $dll
