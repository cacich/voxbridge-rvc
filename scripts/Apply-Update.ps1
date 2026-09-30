param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\VoxBridge')
)

$ErrorActionPreference = 'Stop'
function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose(); $stream.Dispose() }
}
$packageRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$installedRoot = (Resolve-Path -LiteralPath $InstallDir -ErrorAction Stop).Path
$updatePath = Join-Path $packageRoot 'update.json'
$markerPath = Join-Path $installedRoot 'runtime.json'
$exePath = Join-Path $installedRoot 'VoxBridge.exe'

if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    throw '找不到 runtime.json。0.1.0 或 portable 安裝請先使用 0.2.0 完整安裝包。'
}
$update = Get-Content -LiteralPath $updatePath -Raw -Encoding utf8 | ConvertFrom-Json
$installed = Get-Content -LiteralPath $markerPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($update.format -ne 1 -or $installed.format -ne 1 -or $update.runtime_id -ne $installed.runtime_id) {
    throw '執行環境版本不相容，請下載完整安裝包。'
}
if ([Version]$installed.version -lt [Version]$update.minimum_base_version) {
    throw '安裝版本太舊，請先使用完整安裝包。'
}
if ([Version]$installed.version -ge [Version]$update.version) {
    throw '這台電腦已安裝相同或較新的版本。'
}
$installedRuntimeFiles = @($installed.protected_files.PSObject.Properties.Name | Sort-Object)
$requiredRuntimeFiles = @($update.required_runtime_files | Sort-Object)
if (@(Compare-Object -ReferenceObject $installedRuntimeFiles -DifferenceObject $requiredRuntimeFiles).Count -ne 0) {
    throw '更新包需要不同的執行環境檔案，請使用完整安裝包。'
}
if ((Get-Sha256 $exePath) -ne $installed.exe_sha256) {
    throw '原始程式已修改或損壞，請用完整安裝包更新。'
}

$running = Get-CimInstance Win32_Process -Filter "Name='VoxBridge.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($exePath, [StringComparison]::OrdinalIgnoreCase) }
if ($running) { throw '請先關閉 VoxBridge，再執行更新。' }

foreach ($item in $installed.protected_files.PSObject.Properties) {
    $relative = $item.Name
    if ($relative.StartsWith('/') -or $relative.Contains('..') -or $relative.Contains(':')) {
        throw "無效的執行環境檔案路徑：$relative"
    }
    $file = Join-Path $installedRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or
        (Get-Sha256 $file) -ne $item.Value) {
        throw "執行環境檔案不符，請用完整安裝包：$relative"
    }
}

$allowed = @($update.files.PSObject.Properties.Name | Sort-Object)
if ('VoxBridge.exe' -notin $allowed -or '_internal/voxbridge/resources/voxbridge.ico' -notin $allowed) {
    throw '更新包檔案清單不完整。'
}
foreach ($item in $update.files.PSObject.Properties) {
    $relative = $item.Name
    if ($relative -ne 'VoxBridge.exe' -and
        (-not $relative.StartsWith('_internal/voxbridge/') -or $relative.Contains('..') -or $relative.Contains(':'))) {
        throw "更新包包含未核准的檔案：$relative"
    }
    $payload = Join-Path $packageRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $payload -PathType Leaf) -or
        (Get-Sha256 $payload) -ne $item.Value) {
        throw "更新包校驗失敗：$relative"
    }
}

$backupRoot = Join-Path $env:TEMP ('VoxBridge-update-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$backedUp = @()
$newFiles = @()
$markerBackedUp = $false
$success = $false
try {
    foreach ($relative in $allowed) {
        $target = Join-Path $installedRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        $backup = Join-Path $backupRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            Copy-Item -LiteralPath $target -Destination $backup -Force
            $backedUp += $relative
        } else {
            $newFiles += $relative
        }
    }
    Copy-Item -LiteralPath $markerPath -Destination (Join-Path $backupRoot 'runtime.json') -Force
    $markerBackedUp = $true
    $copied = 0
    foreach ($relative in $allowed) {
        $source = Join-Path $packageRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        $target = Join-Path $installedRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target -Force
        $copied++
        if ($env:VOXBRIDGE_UPDATE_TEST_FAIL_AFTER_FIRST_COPY -eq '1' -and $copied -eq 1) {
            throw 'Injected update test failure'
        }
    }
    $installed.version = $update.version
    $installed.exe_sha256 = $update.files.'VoxBridge.exe'
    $installed | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $markerPath -Encoding utf8
    $success = $true
    Write-Host "VoxBridge 已更新至 $($update.version)。設定與模型仍保存在原位置。"
}
catch {
    $originalError = $_
    $restoreErrors = @()
    foreach ($relative in $newFiles) {
        $target = Join-Path $installedRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        try {
            if (Test-Path -LiteralPath $target -PathType Leaf) { Remove-Item -LiteralPath $target -Force }
        } catch { $restoreErrors += $_.Exception.Message }
    }
    foreach ($relative in $backedUp) {
        $source = Join-Path $backupRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        $target = Join-Path $installedRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        try { Copy-Item -LiteralPath $source -Destination $target -Force }
        catch { $restoreErrors += $_.Exception.Message }
    }
    if ($markerBackedUp) {
        try { Copy-Item -LiteralPath (Join-Path $backupRoot 'runtime.json') -Destination $markerPath -Force }
        catch { $restoreErrors += $_.Exception.Message }
    }
    if ($restoreErrors.Count) {
        throw "更新失敗且還原未完成；備份位置：$backupRoot。原始錯誤：$originalError。還原錯誤：$($restoreErrors -join '; ')"
    }
    throw "更新未完成，已還原原檔。備份位置：$backupRoot。錯誤：$originalError"
}
finally {
    if ($success) {
        $resolvedBackup = [IO.Path]::GetFullPath($backupRoot)
        $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
        if ($resolvedBackup.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $resolvedBackup).StartsWith('VoxBridge-update-')) {
            Remove-Item -LiteralPath $resolvedBackup -Recurse -Force
        }
    }
}
