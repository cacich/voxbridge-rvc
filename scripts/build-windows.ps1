param(
    [switch]$SkipInstaller,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvRoot = Join-Path $projectRoot '.venv-build'
$python = Join-Path $venvRoot 'Scripts\python.exe'
$outputRoot = Join-Path $projectRoot 'dist'
$appExe = Join-Path $outputRoot 'VoxBridge\VoxBridge.exe'

if ($env:OS -ne 'Windows_NT') { throw 'Windows 11 x64 build host required.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows required.' }

Push-Location $projectRoot
try {
    $version = (& py -3.12 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
    if ($LASTEXITCODE -ne 0 -or -not $version) { throw 'Unable to read project version.' }
    if (-not (Test-Path $python)) {
        & py -3.12 -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Install it from python.org.' }
    }

    & $python -m pip install --no-cache-dir --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip bootstrap failed.' }
    & $python -m pip install --no-cache-dir 'torch==2.7.1+cu118' 'torchaudio==2.7.1+cu118' --index-url 'https://download.pytorch.org/whl/cu118'
    if ($LASTEXITCODE -ne 0) { throw 'CUDA 11.8 PyTorch installation failed.' }
    & $python -m pip install --no-cache-dir -e '.[dev]' -r 'requirements-engine.txt' -c 'constraints-windows.txt' --index-url 'https://pypi.org/simple'
    if ($LASTEXITCODE -ne 0) { throw 'Application dependencies failed to install.' }

    if (-not $SkipTests) {
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    }

    & $python -m PyInstaller --noconfirm --clean 'packaging\VoxBridge.spec'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    if (-not (Test-Path $appExe)) { throw "Missing built executable: $appExe" }

    $smoke = Start-Process -FilePath $appExe -ArgumentList '--smoke-test' -PassThru -Wait -WindowStyle Hidden
    if ($smoke.ExitCode -ne 0) { throw "Built GUI smoke test failed (exit $($smoke.ExitCode))." }
    $engineReport = Join-Path $outputRoot 'engine-check.json'
    $engineCheck = Start-Process -FilePath $appExe -ArgumentList @('--check-engine', '--check-engine-report', ('"' + $engineReport + '"')) -PassThru -Wait -WindowStyle Hidden
    if ($engineCheck.ExitCode -ne 0) { throw "Bundled inference dependency check failed (exit $($engineCheck.ExitCode))." }
    if (-not (Test-Path -LiteralPath $engineReport)) { throw 'Engine check report was not created.' }
    $engineResult = Get-Content -LiteralPath $engineReport -Raw -Encoding utf8 | ConvertFrom-Json
    if (-not $engineResult.ok) { throw "Bundled inference dependency check failed: $($engineResult.error)" }

    $updatePath = Join-Path $outputRoot "VoxBridge-$version-beta-win64-app-update.zip"
    & $python 'scripts\package-update.py' (Join-Path $outputRoot 'VoxBridge') $updatePath
    if ($LASTEXITCODE -ne 0) { throw 'Application update packaging failed.' }

    $zipPath = Join-Path $outputRoot "VoxBridge-$version-beta-win64-portable.zip"
    & $python 'scripts\make-portable-zip.py' (Join-Path $outputRoot 'VoxBridge') $zipPath
    if ($LASTEXITCODE -ne 0) { throw 'Portable ZIP creation failed.' }

    if (-not $SkipInstaller) {
        $isccCandidates = @()
        if (${env:ProgramFiles(x86)}) { $isccCandidates += Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
        if ($env:ProgramFiles) { $isccCandidates += Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe' }
        $iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $iscc) {
            $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if ($command) { $iscc = $command.Source }
        }
        if (-not $iscc) { throw 'Inno Setup 6 ISCC.exe is required. Run with -SkipInstaller for portable ZIP only.' }
        & $iscc "/DAppVersion=$version" 'packaging\VoxBridge.iss'
        if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
    }

    $manifest = Join-Path $outputRoot 'build-info.txt'
    $revision = (& git rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) { $revision = 'unavailable' }
    @(
        "VoxBridge $version beta Windows x64 build"
        "Git revision: $revision"
        "Built at UTC: $([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))"
        "Python: $(& $python --version)"
        'Package SHA-256:'
    ) | Set-Content -LiteralPath $manifest -Encoding utf8
    Get-ChildItem -LiteralPath $outputRoot -File | Where-Object { $_.Extension -in '.zip', '.exe' } |
        ForEach-Object { "$($_.Name) $((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" } |
        Add-Content -LiteralPath $manifest -Encoding utf8
    'Installed Python dependencies:' | Add-Content -LiteralPath $manifest -Encoding utf8
    & $python -m pip freeze | Add-Content -LiteralPath $manifest -Encoding utf8

    Write-Host "Build finished: $outputRoot"
}
finally {
    Pop-Location
}
