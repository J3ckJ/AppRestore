#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppRestoreArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

foreach ($PythonEnvironmentName in @(
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP"
)) {
    Remove-Item `
        -LiteralPath "Env:$PythonEnvironmentName" `
        -ErrorAction SilentlyContinue
}

# В установленной версии ipatool лежит рядом, в каталоге bin.
$BundledBin = Join-Path $PSScriptRoot "bin"
if (Test-Path -LiteralPath $BundledBin -PathType Container) {
    $env:Path = "$BundledBin;$env:Path"
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$KnownLocalAppData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::LocalApplicationData
)
if ([string]::IsNullOrWhiteSpace($KnownLocalAppData)) {
    throw "Windows Known Folder LocalApplicationData недоступен."
}
$KnownLocalAppData = [System.IO.Path]::GetFullPath($KnownLocalAppData)
$InstalledRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $KnownLocalAppData "Programs\AppRestore")
)
$InstalledEntryPoint = Join-Path $InstalledRoot "bin\apprestore.cmd"
$LocalInstalledEntryPoint = Join-Path $PSScriptRoot "bin\apprestore.cmd"
$Installer = Join-Path $PSScriptRoot "install-windows.ps1"

$EntryPoint = $null
if (Test-Path -LiteralPath $LocalInstalledEntryPoint -PathType Leaf) {
    $EntryPoint = $LocalInstalledEntryPoint
    $BundledBin = Join-Path $PSScriptRoot "bin"
    if (Test-Path -LiteralPath $BundledBin -PathType Container) {
        $env:Path = "$BundledBin;$env:Path"
    }
}

$RunningFromSourceTree = Test-Path -LiteralPath $Installer -PathType Leaf
if ($null -eq $EntryPoint -and $RunningFromSourceTree) {
    Write-Host "Установка или обновление AppRestore…"
    & $Installer
    if (-not (Test-Path -LiteralPath $InstalledEntryPoint -PathType Leaf)) {
        throw "Установщик завершился, но команда AppRestore не найдена."
    }
    $EntryPoint = $InstalledEntryPoint
    $BundledBin = Join-Path $InstalledRoot "bin"
    if (Test-Path -LiteralPath $BundledBin -PathType Container) {
        $env:Path = "$BundledBin;$env:Path"
    }
}
elseif ($null -eq $EntryPoint -and (Test-Path -LiteralPath $InstalledEntryPoint -PathType Leaf)) {
    $EntryPoint = $InstalledEntryPoint
    $BundledBin = Join-Path $InstalledRoot "bin"
    if (Test-Path -LiteralPath $BundledBin -PathType Container) {
        $env:Path = "$BundledBin;$env:Path"
    }
}

if ($null -ne $EntryPoint) {
    # Без аргументов — интерактивное меню (как apprestore.sh на macOS).
    & $EntryPoint @AppRestoreArguments
    exit $LASTEXITCODE
}

# Режим запуска непосредственно из исходников без .venv.
$SourceEntryPoint = Join-Path $PSScriptRoot "apprestore.py"
if (-not (Test-Path -LiteralPath $SourceEntryPoint -PathType Leaf)) {
    throw "Не найден AppRestore: ни установленная команда, ни apprestore.py."
}

$SupportedPythonCheck = (
    "import sys; raise SystemExit(0 if " +
    "(3, 10) <= sys.version_info < (3, 14) else 1)"
)
$PythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
if ($null -ne $PythonLauncher) {
    foreach ($Selector in @("-3.13", "-3.12", "-3.11", "-3.10")) {
        & $PythonLauncher.Source $Selector -c $SupportedPythonCheck *> $null
        if ($LASTEXITCODE -eq 0) {
            & $PythonLauncher.Source `
                $Selector `
                $SourceEntryPoint `
                @AppRestoreArguments
            exit $LASTEXITCODE
        }
    }
}

$Python = Get-Command "python.exe" -ErrorAction SilentlyContinue
if ($null -ne $Python) {
    & $Python.Source -c $SupportedPythonCheck *> $null
    if ($LASTEXITCODE -eq 0) {
        & $Python.Source $SourceEntryPoint @AppRestoreArguments
        exit $LASTEXITCODE
    }
}

throw "Нужен Python версии 3.10–3.13. Установите Python и повторите запуск."
