#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$NoPathUpdate,
    [switch]$SkipAppleBridge
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$AppRestoreVersion = "0.1.3"
$IpaToolVersion = "2.3.1"
$IpaToolSha256 = "8e986ed9320f205bcd1fd24640ec46a5b92ff346425aff28d1103e57d2fdcadb"
$IpaToolUrl = "https://github.com/majd/ipatool/releases/download/v$IpaToolVersion/ipatool-$IpaToolVersion-windows-amd64.tar.gz"
$PythonInstallerVersion = "3.12.10"
$PythonInstallerSha256 = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
$PythonInstallerUrl = "https://www.python.org/ftp/python/$PythonInstallerVersion/python-$PythonInstallerVersion-amd64.exe"

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "Переменная LOCALAPPDATA не определена: невозможно выбрать пользовательский каталог."
}

$InstallRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA "Programs\AppRestore")
)
$ExpectedRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA "Programs\AppRestore")
)
if (-not [string]::Equals(
    $InstallRoot,
    $ExpectedRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Отказ: вычисленный каталог установки не совпадает с ожидаемым."
}

if (Test-Path -LiteralPath $InstallRoot) {
    $InstallRootItem = Get-Item -LiteralPath $InstallRoot -Force
    if (($InstallRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Отказ: каталог установки является ссылкой или точкой повторной обработки."
    }
}

$Architecture = $env:PROCESSOR_ARCHITEW6432
if ([string]::IsNullOrWhiteSpace($Architecture)) {
    $Architecture = $env:PROCESSOR_ARCHITECTURE
}
if (-not [string]::Equals(
    $Architecture,
    "AMD64",
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Этот установщик содержит проверенный ipatool для Windows x64 (AMD64)."
}

$RequiredSourceFiles = @(
    "pyproject.toml",
    "LICENSE",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "apprestore.py",
    "apprestore.ps1",
    "uninstall-windows.ps1"
)
foreach ($RelativeFile in $RequiredSourceFiles) {
    $SourceFile = Join-Path $PSScriptRoot $RelativeFile
    if (-not (Test-Path -LiteralPath $SourceFile -PathType Leaf)) {
        throw "Неполный комплект установки: отсутствует $RelativeFile."
    }
}
$SourceCore = Join-Path $PSScriptRoot "apprestore_core"
if (-not (Test-Path -LiteralPath $SourceCore -PathType Container)) {
    throw "Неполный комплект установки: отсутствует apprestore_core."
}

function Find-CompatiblePython {
    $VersionCheck = (
        "import struct, sys; " +
        "raise SystemExit(0 if sys.version_info >= (3, 10) " +
        "and struct.calcsize('P') * 8 == 64 else 1)"
    )
    $KnownPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    $KnownPythons = @()
    if (Test-Path -LiteralPath $KnownPythonRoot -PathType Container) {
        $KnownPythons = @(
            Get-ChildItem -LiteralPath $KnownPythonRoot -Directory -Filter "Python3*" |
                Sort-Object -Property Name -Descending |
                ForEach-Object { Join-Path $_.FullName "python.exe" }
        )
    }
    foreach ($KnownPython in $KnownPythons) {
        if (-not (Test-Path -LiteralPath $KnownPython -PathType Leaf)) {
            continue
        }
        $PyExit = -1
        & $KnownPython -c $VersionCheck *> $null
        $PyExit = $LASTEXITCODE
        if ($PyExit -eq 0) {
            return [pscustomobject]@{
                Executable = $KnownPython
                Prefix = @()
            }
        }
    }

    $Launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $Launcher) {
        # Сначала предпочитаем распространённые стабильные версии, затем любую Python 3.
        foreach ($Selector in @("-3.13", "-3.12", "-3.11", "-3.10", "-3")) {
            $PyExit = -1
            $PreviousErrorAction = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & $Launcher.Source $Selector -c $VersionCheck *> $null
                $PyExit = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $PreviousErrorAction
            }
            if ($PyExit -eq 0) {
                return [pscustomobject]@{
                    Executable = $Launcher.Source
                    Prefix = @($Selector)
                }
            }
        }
    }

    $PythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $PythonCommand) {
        $PyExit = -1
        $PreviousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $PythonCommand.Source -c $VersionCheck *> $null
            $PyExit = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $PreviousErrorAction
        }
        if ($PyExit -eq 0) {
            return [pscustomobject]@{
                Executable = $PythonCommand.Source
                Prefix = @()
            }
        }
    }

    return $null
}

function Install-CompatiblePython {
    $Winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($null -ne $Winget) {
        Write-Host "Python 3.10+ не найден. Установка Python 3.12 для текущего пользователя…"
        & $Winget.Source install `
            -e `
            --id Python.Python.3.12 `
            --scope user `
            --silent `
            --accept-package-agreements `
            --accept-source-agreements `
            --disable-interactivity
        $WingetCode = $LASTEXITCODE
        if ($WingetCode -in @(0, -1978335189, -1978334964)) {
            if ($null -ne (Find-CompatiblePython)) {
                return
            }
            Write-Warning (
                "winget завершился успешно, но 64-битный Python не найден. " +
                "Пробуем официальный установщик Python."
            )
        }
        else {
            Write-Warning (
                "winget не смог установить Python 3.12 (код $WingetCode). " +
                "Пробуем официальный установщик Python."
            )
        }
    }

    $PythonTempRoot = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) ("AppRestore-python-" + [guid]::NewGuid().ToString("N"))
    $PythonInstaller = Join-Path $PythonTempRoot (
        "python-$PythonInstallerVersion-amd64.exe"
    )
    try {
        New-Item -ItemType Directory -Path $PythonTempRoot -Force | Out-Null
        Write-Host (
            "Скачивание официального Python $PythonInstallerVersion " +
            "для текущего пользователя…"
        )
        $PreviousSecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol
        try {
            [System.Net.ServicePointManager]::SecurityProtocol = (
                $PreviousSecurityProtocol -bor
                [System.Net.SecurityProtocolType]::Tls12
            )
            Invoke-WebRequest `
                -Uri $PythonInstallerUrl `
                -OutFile $PythonInstaller `
                -UseBasicParsing
        }
        finally {
            [System.Net.ServicePointManager]::SecurityProtocol = (
                $PreviousSecurityProtocol
            )
        }

        $ActualPythonSha256 = (
            Get-FileHash -LiteralPath $PythonInstaller -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if (-not [string]::Equals(
            $ActualPythonSha256,
            $PythonInstallerSha256,
            [System.StringComparison]::Ordinal
        )) {
            throw (
                "SHA-256 установщика Python не совпал. Ожидался " +
                "$PythonInstallerSha256, получен $ActualPythonSha256."
            )
        }

        $PythonTarget = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
        & $PythonInstaller `
            /quiet `
            InstallAllUsers=0 `
            Include_doc=0 `
            Include_launcher=0 `
            Include_test=0 `
            PrependPath=0 `
            Shortcuts=0 `
            "TargetDir=$PythonTarget"
        $PythonInstallerCode = $LASTEXITCODE
        if ($PythonInstallerCode -notin @(0, 3010)) {
            throw (
                "Официальный установщик Python завершился с кодом " +
                "$PythonInstallerCode."
            )
        }
    }
    finally {
        if (Test-Path -LiteralPath $PythonTempRoot) {
            $ResolvedPythonTemp = [System.IO.Path]::GetFullPath($PythonTempRoot)
            $SystemTempRoot = [System.IO.Path]::GetFullPath(
                [System.IO.Path]::GetTempPath()
            ).TrimEnd("\")
            $PythonTempLeaf = [System.IO.Path]::GetFileName($ResolvedPythonTemp)
            $PythonTempParent = [System.IO.Path]::GetDirectoryName(
                $ResolvedPythonTemp
            )
            if (
                [string]::Equals(
                    $PythonTempParent,
                    $SystemTempRoot,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -and
                $PythonTempLeaf.StartsWith(
                    "AppRestore-python-",
                    [System.StringComparison]::Ordinal
                )
            ) {
                Remove-Item -LiteralPath $ResolvedPythonTemp -Recurse -Force
            }
        }
    }
}

$SelectedPython = Find-CompatiblePython
if ($null -eq $SelectedPython) {
    Install-CompatiblePython
    $SelectedPython = Find-CompatiblePython
}
if ($null -eq $SelectedPython) {
    throw "Python 3.12 установлен, но исполняемый файл не найден."
}

function Invoke-SelectedPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $AllArguments = @()
    $AllArguments += @($SelectedPython.Prefix)
    $AllArguments += $Arguments
    & $SelectedPython.Executable @AllArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python завершился с кодом $LASTEXITCODE."
    }
}

$BitnessCheck = "import struct; raise SystemExit(0 if struct.calcsize('P') * 8 == 64 else 1)"
$BitnessArguments = @()
$BitnessArguments += @($SelectedPython.Prefix)
$BitnessArguments += @("-c", $BitnessCheck)
& $SelectedPython.Executable @BitnessArguments *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Нужен 64-битный Python."
}

function Test-AppleUsbmuxPort {
    try {
        $Client = New-Object System.Net.Sockets.TcpClient
        $Async = $Client.BeginConnect("127.0.0.1", 27015, $null, $null)
        $Waited = $Async.AsyncWaitHandle.WaitOne(1000, $false)
        if (-not $Waited) {
            $Client.Close()
            return $false
        }
        $Client.EndConnect($Async)
        $Client.Close()
        return $true
    }
    catch {
        return $false
    }
}

function Ensure-AppleBridge {
    if (Test-AppleUsbmuxPort) {
        Write-Host "Apple USB-мост уже доступен (127.0.0.1:27015)."
        return
    }

    foreach ($ServiceName in @("Apple Mobile Device Service", "Apple Mobile Device")) {
        $Query = & sc.exe query $ServiceName 2>$null
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        Write-Host "Запуск службы $ServiceName…"
        & sc.exe start $ServiceName *> $null
        for ($Index = 0; $Index -lt 20; $Index++) {
            if (Test-AppleUsbmuxPort) {
                Write-Host "Apple USB-мост готов."
                return
            }
            Start-Sleep -Seconds 1
        }
    }

    $Winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($null -eq $Winget) {
        Write-Warning (
            "winget не найден. Установите Apple Devices/iTunes или Apple Mobile Device Support, " +
            "затем выполните: apprestore setup"
        )
        return
    }

    Write-Host "Установка Apple Mobile Device Support через winget (может появиться UAC)…"
    & $Winget.Source install `
        -e `
        --id Apple.AppleMobileDeviceSupport `
        --accept-package-agreements `
        --accept-source-agreements `
        --disable-interactivity
    $WingetCode = $LASTEXITCODE
    # 0 = OK; распространённые коды «уже установлено».
    if ($WingetCode -notin @(0, -1978335189, -1978334964)) {
        Write-Host "Повтор с повышением прав…"
        try {
            $Elevated = Start-Process `
                -FilePath $Winget.Source `
                -ArgumentList @(
                    "install",
                    "-e",
                    "--id",
                    "Apple.AppleMobileDeviceSupport",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                    "--disable-interactivity"
                ) `
                -Verb RunAs `
                -Wait `
                -PassThru
        }
        catch {
            Write-Warning (
                "Повышение прав отменено или недоступно. " +
                "AppRestore установлен; Apple USB-мост можно настроить позже: " +
                "apprestore setup"
            )
            return
        }
        if ($Elevated.ExitCode -notin @(0, -1978335189, -1978334964)) {
            Write-Warning (
                "Не удалось поставить Apple.AppleMobileDeviceSupport автоматически. " +
                "После установки Apple Devices/iTunes выполните: apprestore setup"
            )
            return
        }
    }

    foreach ($ServiceName in @("Apple Mobile Device Service", "Apple Mobile Device")) {
        & sc.exe start $ServiceName *> $null
    }
    for ($Index = 0; $Index -lt 45; $Index++) {
        if (Test-AppleUsbmuxPort) {
            Write-Host "Apple USB-мост готов."
            return
        }
        Start-Sleep -Seconds 1
    }
    Write-Warning (
        "Apple Mobile Device Support установлен, но порт 27015 пока не отвечает. " +
        "Подключите iPhone, разблокируйте и подтвердите «Доверять», затем: apprestore setup"
    )
}

$TempRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("AppRestore-install-" + [guid]::NewGuid().ToString("N"))
$ArchivePath = Join-Path $TempRoot "ipatool.tar.gz"
$ExtractPath = Join-Path $TempRoot "ipatool"

try {
    New-Item -ItemType Directory -Path $ExtractPath -Force | Out-Null

    Write-Host "Скачивание официального ipatool v$IpaToolVersion..."
    $PreviousSecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = (
            $PreviousSecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12
        )
        Invoke-WebRequest `
            -Uri $IpaToolUrl `
            -OutFile $ArchivePath `
            -UseBasicParsing
    }
    finally {
        [System.Net.ServicePointManager]::SecurityProtocol = $PreviousSecurityProtocol
    }

    $ActualSha256 = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not [string]::Equals(
        $ActualSha256,
        $IpaToolSha256,
        [System.StringComparison]::Ordinal
    )) {
        throw "SHA-256 ipatool не совпал. Ожидался $IpaToolSha256, получен $ActualSha256."
    }

    $ExpectedIpaToolName = "ipatool-$IpaToolVersion-windows-amd64.exe"
    $SafeTarExtract = @'
import pathlib
import shutil
import sys
import tarfile

archive_path = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
expected_name = sys.argv[3]
with tarfile.open(archive_path, mode="r:gz") as archive:
    matches = [
        member
        for member in archive.getmembers()
        if member.isfile()
        and pathlib.PurePosixPath(member.name).name == expected_name
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one {expected_name!r}, found {len(matches)}"
        )
    source = archive.extractfile(matches[0])
    if source is None:
        raise SystemExit("could not read the expected ipatool member")
    target = destination / expected_name
    with source, target.open("wb") as output:
        shutil.copyfileobj(source, output)
'@
    Invoke-SelectedPython -Arguments @(
        "-c",
        $SafeTarExtract,
        $ArchivePath,
        $ExtractPath,
        $ExpectedIpaToolName
    )
    $ExtractedIpaTools = @(
        Get-ChildItem -LiteralPath $ExtractPath -Recurse -File -Filter $ExpectedIpaToolName
    )
    if ($ExtractedIpaTools.Count -ne 1) {
        throw "В проверенном архиве ожидался ровно один $ExpectedIpaToolName."
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    $SourceTarget = Join-Path $InstallRoot "src"
    $CoreTarget = Join-Path $SourceTarget "apprestore_core"
    $BinTarget = Join-Path $InstallRoot "bin"
    $VenvTarget = Join-Path $InstallRoot ".venv"

    foreach ($ManagedDirectory in @($SourceTarget, $BinTarget, $VenvTarget)) {
        if (Test-Path -LiteralPath $ManagedDirectory) {
            $ManagedItem = Get-Item -LiteralPath $ManagedDirectory -Force
            if (($ManagedItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Отказ: управляемый каталог является ссылкой: $ManagedDirectory"
            }
        }
    }

    if (Test-Path -LiteralPath $SourceTarget) {
        Remove-Item -LiteralPath $SourceTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Path $CoreTarget -Force | Out-Null
    New-Item -ItemType Directory -Path $BinTarget -Force | Out-Null

    foreach ($RelativeFile in @(
        "pyproject.toml",
        "LICENSE",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "apprestore.py"
    )) {
        Copy-Item `
            -LiteralPath (Join-Path $PSScriptRoot $RelativeFile) `
            -Destination (Join-Path $SourceTarget $RelativeFile) `
            -Force
    }
    Get-ChildItem -LiteralPath $SourceCore -File -Filter "*.py" |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $CoreTarget -Force
        }

    Copy-Item `
        -LiteralPath $ExtractedIpaTools[0].FullName `
        -Destination (Join-Path $BinTarget "ipatool.exe") `
        -Force

    Copy-Item `
        -LiteralPath (Join-Path $PSScriptRoot "apprestore.ps1") `
        -Destination (Join-Path $InstallRoot "apprestore.ps1") `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $PSScriptRoot "uninstall-windows.ps1") `
        -Destination (Join-Path $InstallRoot "uninstall-windows.ps1") `
        -Force

    if (-not (Test-Path -LiteralPath (Join-Path $VenvTarget "Scripts\python.exe") -PathType Leaf)) {
        Invoke-SelectedPython -Arguments @("-m", "venv", $VenvTarget)
    }

    $VenvPython = Join-Path $VenvTarget "Scripts\python.exe"
    & $VenvPython `
        -m pip install `
        --disable-pip-version-check `
        --upgrade `
        $SourceTarget
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось установить AppRestore и его Python-зависимости."
    }

    $CommandWrapper = @'
@echo off
setlocal
set "PATH=%~dp0;%PATH%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0..\.venv\Scripts\apprestore.exe" %*
exit /b %ERRORLEVEL%
'@
    $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        (Join-Path $BinTarget "apprestore.cmd"),
        $CommandWrapper,
        $Utf8WithoutBom
    )

    if (-not $NoPathUpdate) {
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($null -eq $UserPath) {
            $UserPath = ""
        }
        $BinForComparison = $BinTarget.TrimEnd("\")
        $PathContainsBin = $false
        foreach ($Segment in ($UserPath -split ";")) {
            $CleanSegment = $Segment.Trim().Trim('"').TrimEnd("\")
            if ([string]::Equals(
                $CleanSegment,
                $BinForComparison,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                $PathContainsBin = $true
                break
            }
        }
        if (-not $PathContainsBin) {
            $NewUserPath = $BinTarget
            if (-not [string]::IsNullOrWhiteSpace($UserPath)) {
                $NewUserPath = $UserPath.TrimEnd(";") + ";" + $BinTarget
            }
            [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
        }

        $ProcessContainsBin = $false
        foreach ($Segment in ($env:Path -split ";")) {
            $CleanSegment = $Segment.Trim().Trim('"').TrimEnd("\")
            if ([string]::Equals(
                $CleanSegment,
                $BinForComparison,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                $ProcessContainsBin = $true
                break
            }
        }
        if (-not $ProcessContainsBin) {
            $env:Path = $BinTarget + ";" + $env:Path
        }
    }

    if (-not $SkipAppleBridge) {
        Write-Host ""
        try {
            Ensure-AppleBridge
        }
        catch {
            Write-Warning (
                "AppRestore установлен, но Apple USB-мост не удалось настроить: " +
                "$($_.Exception.Message) Выполните позже: apprestore setup"
            )
        }
    }

    Write-Host ""
    Write-Host "AppRestore $AppRestoreVersion установлен в:"
    Write-Host "  $InstallRoot"
    if ($NoPathUpdate) {
        Write-Host "Запуск меню:"
        Write-Host "  & `"$InstallRoot\apprestore.ps1`""
    }
    else {
        Write-Host "Запуск:"
        Write-Host "  apprestore"
        Write-Host "Команда уже доступна в этом терминале."
    }
}
finally {
    if (Test-Path -LiteralPath $TempRoot) {
        $ExpectedTempRoot = [System.IO.Path]::GetFullPath($TempRoot)
        $SystemTempRoot = [System.IO.Path]::GetFullPath(
            [System.IO.Path]::GetTempPath()
        ).TrimEnd("\")
        if ($ExpectedTempRoot.StartsWith(
            $SystemTempRoot + "\",
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            Remove-Item -LiteralPath $ExpectedTempRoot -Recurse -Force
        }
    }
}
