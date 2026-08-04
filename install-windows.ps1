#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$NoPathUpdate,
    [switch]$SkipAppleBridge
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$AppRestoreVersion = "0.1.6"
$ManagedInstallMarkerName = ".apprestore-managed"
$ManagedInstallMarkerValue = "AppRestore managed installation v1"
$IpaToolVersion = "2.3.1"
$IpaToolSha256 = "8e986ed9320f205bcd1fd24640ec46a5b92ff346425aff28d1103e57d2fdcadb"
$IpaToolUrl = "https://github.com/majd/ipatool/releases/download/v$IpaToolVersion/ipatool-$IpaToolVersion-windows-amd64.tar.gz"
$PythonInstallerVersion = "3.12.10"
$PythonInstallerSha256 = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
$PythonInstallerUrl = "https://www.python.org/ftp/python/$PythonInstallerVersion/python-$PythonInstallerVersion-amd64.exe"

$KnownLocalAppData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::LocalApplicationData
)
if ([string]::IsNullOrWhiteSpace($KnownLocalAppData)) {
    throw "Windows Known Folder LocalApplicationData недоступен."
}
$KnownLocalAppData = [System.IO.Path]::GetFullPath($KnownLocalAppData)
$InstallRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $KnownLocalAppData "Programs\AppRestore")
)
$ProgramsRoot = [System.IO.Path]::GetDirectoryName($InstallRoot)

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

function Assert-PlainDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $Item = Get-Item -LiteralPath $Path -Force
    if (-not $Item.PSIsContainer) {
        throw "$Label не является каталогом: $Path"
    }
    if (
        ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Label не может быть ссылкой или reparse point: $Path"
    }
}

function Ensure-PlainDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
    Assert-PlainDirectory -Path $Path -Label $Label
}

Assert-PlainDirectory `
    -Path $KnownLocalAppData `
    -Label "Windows Known Folder LocalApplicationData"
Ensure-PlainDirectory `
    -Path $ProgramsRoot `
    -Label "Каталог программ пользователя"
if (Test-Path -LiteralPath $InstallRoot) {
    Assert-PlainDirectory `
        -Path $InstallRoot `
        -Label "Каталог установки AppRestore"
}

function Find-CompatiblePython {
    $VersionCheck = (
        "import struct, sys; " +
        "raise SystemExit(0 if sys.version_info >= (3, 10) " +
        "and struct.calcsize('P') * 8 == 64 else 1)"
    )
    $KnownPythonRoot = Join-Path $KnownLocalAppData "Programs\Python"
    $KnownPythons = @()
    if (Test-Path -LiteralPath $KnownPythonRoot) {
        Assert-PlainDirectory `
            -Path $KnownPythonRoot `
            -Label "Каталог Python пользователя"
        $KnownPythons = @(
            Get-ChildItem -LiteralPath $KnownPythonRoot -Directory -Filter "Python3*" |
                Sort-Object -Property Name -Descending |
                ForEach-Object {
                    Assert-PlainDirectory `
                        -Path $_.FullName `
                        -Label "Каталог установленного Python"
                    Join-Path $_.FullName "python.exe"
                }
        )
    }
    foreach ($KnownPython in $KnownPythons) {
        if (-not (Test-Path -LiteralPath $KnownPython -PathType Leaf)) {
            continue
        }
        $KnownPythonItem = Get-Item -LiteralPath $KnownPython -Force
        if (
            ($KnownPythonItem.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "python.exe в управляемом каталоге является reparse point."
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
    $PythonProgramsRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $ProgramsRoot "Python")
    )
    Ensure-PlainDirectory `
        -Path $ProgramsRoot `
        -Label "Каталог программ пользователя"
    Ensure-PlainDirectory `
        -Path $PythonProgramsRoot `
        -Label "Каталог Python пользователя"
    $PythonTarget = [System.IO.Path]::GetFullPath(
        (Join-Path $PythonProgramsRoot "Python312")
    )
    Ensure-PlainDirectory `
        -Path $PythonTarget `
        -Label "Каталог Python 3.12"

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

function Invoke-AppRestoreInstallTransaction {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$StagingRoot,
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot,
        [Parameter(Mandatory = $true)]
        [string]$BackupRoot,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerName,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerValue,
        [Parameter(Mandatory = $true)]
        [scriptblock]$PrepareStaging,
        [Parameter(Mandatory = $true)]
        [scriptblock]$VerifyStaging,
        [Parameter(Mandatory = $true)]
        [scriptblock]$VerifyInstallation
    )

    $ResolvedInstall = [System.IO.Path]::GetFullPath($InstallRoot)
    $ResolvedStaging = [System.IO.Path]::GetFullPath($StagingRoot)
    $ResolvedBackup = [System.IO.Path]::GetFullPath($BackupRoot)
    $ProgramsRoot = [System.IO.Path]::GetDirectoryName($ResolvedInstall)
    $InstallLeaf = [System.IO.Path]::GetFileName($ResolvedInstall)
    $StagingLeaf = [System.IO.Path]::GetFileName($ResolvedStaging)
    $BackupLeaf = [System.IO.Path]::GetFileName($ResolvedBackup)

    if (-not [string]::Equals(
        $InstallLeaf,
        "AppRestore",
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Некорректное имя live-каталога AppRestore."
    }
    foreach ($Candidate in @($ResolvedStaging, $ResolvedBackup)) {
        if (-not [string]::Equals(
            [System.IO.Path]::GetDirectoryName($Candidate),
            $ProgramsRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Staging и backup должны находиться рядом с live-каталогом."
        }
    }
    if (
        -not $StagingLeaf.StartsWith(
            "AppRestore.staging-",
            [System.StringComparison]::Ordinal
        ) -or
        -not $BackupLeaf.StartsWith(
            "AppRestore.backup-",
            [System.StringComparison]::Ordinal
        )
    ) {
        throw "Некорректные имена staging/backup AppRestore."
    }
    if (-not (Test-Path -LiteralPath $ResolvedStaging -PathType Container)) {
        throw "Не найден подготовленный staging-каталог AppRestore."
    }
    if (Test-Path -LiteralPath $ResolvedBackup) {
        throw "Backup-каталог AppRestore уже существует."
    }

    $AssertPlainTree = {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Path,
            [Parameter(Mandatory = $true)]
            [string]$Label
        )
        if (-not (Test-Path -LiteralPath $Path)) {
            return
        }
        $RootItem = Get-Item -LiteralPath $Path -Force
        if (-not $RootItem.PSIsContainer) {
            throw "$Label должен быть каталогом: $Path"
        }
        if (
            ($RootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne
            0
        ) {
            throw "$Label является ссылкой или точкой повторной обработки: $Path"
        }
        $NestedReparse = Get-ChildItem `
            -LiteralPath $Path `
            -Force `
            -Recurse `
            -ErrorAction Stop |
            Where-Object {
                ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            } |
            Select-Object -First 1
        if ($null -ne $NestedReparse) {
            throw "$Label содержит ссылку: $($NestedReparse.FullName)"
        }
    }

    $AssertManagedInstall = {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Path
        )

        & $AssertPlainTree -Path $Path -Label "Текущая установка"
        $MarkerPath = Join-Path $Path $ManagedMarkerName
        $HasExactMarker = (
            (Test-Path -LiteralPath $MarkerPath -PathType Leaf) -and
            [string]::Equals(
                [System.IO.File]::ReadAllText($MarkerPath),
                $ManagedMarkerValue,
                [System.StringComparison]::Ordinal
            )
        )
        if ($HasExactMarker) {
            return
        }

        $GetSha256 = {
            param([Parameter(Mandatory = $true)][string]$FilePath)

            $Stream = [System.IO.File]::OpenRead($FilePath)
            $Algorithm = [System.Security.Cryptography.SHA256]::Create()
            try {
                return (
                    [System.BitConverter]::ToString(
                        $Algorithm.ComputeHash($Stream)
                    ).Replace("-", "").ToLowerInvariant()
                )
            }
            finally {
                $Algorithm.Dispose()
                $Stream.Dispose()
            }
        }
        $LegacyV013Hashes = [ordered]@{
            "apprestore.ps1" = "9ee7beff4201448cc44f10cd6a135c4c74bbbc34b5f63e55534e9c562d498d80"
            "uninstall-windows.ps1" = "a0236a380ce568e15603b311b593ba37e7d3674d2750d16b14c2ae1a39d1c5b4"
            "src\apprestore.py" = "2ba4e1da347b33a5a698473f1d961b3dfc4074435eb52dbb41ccf772bf60f33c"
            "src\apprestore_core\__init__.py" = "b04720f00147032b00bc8fb0c1200eccbd5f7c316757d9bba72bc0e10fa2098e"
            "src\pyproject.toml" = "e4df42479fbe74a45a2fcf669828486fd6acf438709971542650ab6346e48936"
        }
        foreach ($LegacyRelativePath in $LegacyV013Hashes.Keys) {
            $LegacyFile = Join-Path $Path $LegacyRelativePath
            if (-not (Test-Path -LiteralPath $LegacyFile -PathType Leaf)) {
                throw "Отказ: $Path не является известной установкой AppRestore v0.1.3."
            }
            $LegacyHash = & $GetSha256 -FilePath $LegacyFile
            if (-not [string]::Equals(
                $LegacyHash,
                $LegacyV013Hashes[$LegacyRelativePath],
                [System.StringComparison]::Ordinal
            )) {
                throw "Отказ: fingerprint AppRestore v0.1.3 не совпал: $LegacyRelativePath"
            }
        }

        $LegacyCommandWrapper = @'
@echo off
setlocal
set "PATH=%~dp0;%PATH%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0..\.venv\Scripts\apprestore.exe" %*
exit /b %ERRORLEVEL%
'@
        $LegacyCommand = Join-Path $Path "bin\apprestore.cmd"
        if (
            -not (Test-Path -LiteralPath $LegacyCommand -PathType Leaf) -or
            -not [string]::Equals(
                [System.IO.File]::ReadAllText($LegacyCommand),
                $LegacyCommandWrapper,
                [System.StringComparison]::Ordinal
            )
        ) {
            throw "Отказ: launcher AppRestore v0.1.3 не совпал."
        }
        foreach ($LegacyRuntimeFile in @(
            (Join-Path $Path ".venv\Scripts\python.exe"),
            (Join-Path $Path ".venv\Scripts\apprestore.exe"),
            (Join-Path $Path "bin\ipatool.exe")
        )) {
            if (-not (Test-Path -LiteralPath $LegacyRuntimeFile -PathType Leaf)) {
                throw "Отказ: runtime fingerprint AppRestore v0.1.3 неполон."
            }
        }
    }

    $RemovePlainTree = {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Path,
            [Parameter(Mandatory = $true)]
            [string]$Label
        )
        if (Test-Path -LiteralPath $Path) {
            & $AssertPlainTree -Path $Path -Label $Label
            Remove-Item -LiteralPath $Path -Recurse -Force
        }
    }

    $BackupCreated = $false
    $InstallCommitted = $false
    $Success = $false
    try {
        & $AssertPlainTree -Path $ResolvedStaging -Label "Staging AppRestore"
        & $PrepareStaging $ResolvedStaging
        & $AssertPlainTree -Path $ResolvedStaging -Label "Staging AppRestore"
        & $VerifyStaging $ResolvedStaging

        if (Test-Path -LiteralPath $ResolvedInstall) {
            & $AssertManagedInstall -Path $ResolvedInstall
            Move-Item -LiteralPath $ResolvedInstall -Destination $ResolvedBackup
            $BackupCreated = $true
        }

        Move-Item -LiteralPath $ResolvedStaging -Destination $ResolvedInstall
        $InstallCommitted = $true
        & $VerifyInstallation $ResolvedInstall
        $Success = $true
    }
    catch {
        $Failure = $_
        try {
            if ($InstallCommitted -and (Test-Path -LiteralPath $ResolvedInstall)) {
                & $RemovePlainTree `
                    -Path $ResolvedInstall `
                    -Label "Незавершённая установка"
            }
            if ($BackupCreated -and (Test-Path -LiteralPath $ResolvedBackup)) {
                Move-Item -LiteralPath $ResolvedBackup -Destination $ResolvedInstall
                $BackupCreated = $false
            }
        }
        catch {
            throw (
                "Установка завершилась ошибкой: $($Failure.Exception.Message) " +
                "Автоматический rollback также не удался: " +
                "$($_.Exception.Message). Backup: $ResolvedBackup"
            )
        }
        throw $Failure
    }
    finally {
        if (Test-Path -LiteralPath $ResolvedStaging) {
            & $RemovePlainTree -Path $ResolvedStaging -Label "Staging AppRestore"
        }
        if (
            $Success -and
            $BackupCreated -and
            (Test-Path -LiteralPath $ResolvedBackup)
        ) {
            try {
                & $RemovePlainTree -Path $ResolvedBackup -Label "Backup AppRestore"
                $BackupCreated = $false
            }
            catch {
                Write-Warning (
                    "Новая версия AppRestore установлена и проверена, но " +
                    "предыдущий backup не удалось удалить: $ResolvedBackup. " +
                    $_.Exception.Message
                )
            }
        }
    }
}

$TempRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("AppRestore-install-" + [guid]::NewGuid().ToString("N"))
$ArchivePath = Join-Path $TempRoot "ipatool.tar.gz"
$ExtractPath = Join-Path $TempRoot "ipatool"
$TransactionId = [guid]::NewGuid().ToString("N")
$StagingRoot = Join-Path $ProgramsRoot "AppRestore.staging-$TransactionId"
$BackupRoot = Join-Path $ProgramsRoot "AppRestore.backup-$TransactionId"

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

    Ensure-PlainDirectory `
        -Path $ProgramsRoot `
        -Label "Каталог программ пользователя"
    New-Item -ItemType Directory -Path $StagingRoot | Out-Null
    $SourceTarget = Join-Path $StagingRoot "src"
    $CoreTarget = Join-Path $SourceTarget "apprestore_core"
    $BinTarget = Join-Path $StagingRoot "bin"
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
        -Destination (Join-Path $StagingRoot "apprestore.ps1") `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $PSScriptRoot "uninstall-windows.ps1") `
        -Destination (Join-Path $StagingRoot "uninstall-windows.ps1") `
        -Force

    $ExpectedIpaToolExecutableSha256 = (
        Get-FileHash `
            -LiteralPath $ExtractedIpaTools[0].FullName `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    $PrepareStaging = {
        param([string]$PreparedRoot)

        $PreparedSource = Join-Path $PreparedRoot "src"
        $PreparedBin = Join-Path $PreparedRoot "bin"
        $PreparedVenv = Join-Path $PreparedRoot ".venv"
        Invoke-SelectedPython -Arguments @("-m", "venv", $PreparedVenv)

        $PreparedVenvPython = Join-Path $PreparedVenv "Scripts\python.exe"
        & $PreparedVenvPython `
            -m pip install `
            --disable-pip-version-check `
            --no-input `
            --upgrade `
            --force-reinstall `
            $PreparedSource
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить AppRestore и его Python-зависимости."
        }

        $CommandWrapper = @'
@echo off
setlocal
set "PATH=%~dp0;%PATH%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0..\.venv\Scripts\python.exe" -m apprestore_core.cli %*
exit /b %ERRORLEVEL%
'@
        $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText(
            (Join-Path $PreparedBin "apprestore.cmd"),
            $CommandWrapper,
            $Utf8WithoutBom
        )
        [System.IO.File]::WriteAllText(
            (Join-Path $PreparedRoot $ManagedInstallMarkerName),
            $ManagedInstallMarkerValue,
            $Utf8WithoutBom
        )
    }

    $VerifyStaging = {
        param([string]$PreparedRoot)

        $PreparedSource = Join-Path $PreparedRoot "src"
        $PreparedBin = Join-Path $PreparedRoot "bin"
        $RequiredPreparedFiles = @(
            (Join-Path $PreparedRoot $ManagedInstallMarkerName),
            (Join-Path $PreparedRoot "apprestore.ps1"),
            (Join-Path $PreparedRoot "uninstall-windows.ps1"),
            (Join-Path $PreparedSource "pyproject.toml"),
            (Join-Path $PreparedSource "apprestore.py"),
            (Join-Path $PreparedSource "apprestore_core\__init__.py"),
            (Join-Path $PreparedBin "ipatool.exe"),
            (Join-Path $PreparedBin "apprestore.cmd"),
            (Join-Path $PreparedRoot ".venv\Scripts\python.exe")
        )
        foreach ($RequiredPreparedFile in $RequiredPreparedFiles) {
            if (-not (Test-Path -LiteralPath $RequiredPreparedFile -PathType Leaf)) {
                throw "Проверка staging: отсутствует $RequiredPreparedFile"
            }
        }
        $MarkerValue = [System.IO.File]::ReadAllText(
            (Join-Path $PreparedRoot $ManagedInstallMarkerName)
        )
        if (-not [string]::Equals(
            $MarkerValue,
            $ManagedInstallMarkerValue,
            [System.StringComparison]::Ordinal
        )) {
            throw "Проверка staging: marker AppRestore повреждён."
        }

        $PreparedIpaToolSha256 = (
            Get-FileHash `
                -LiteralPath (Join-Path $PreparedBin "ipatool.exe") `
                -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if (-not [string]::Equals(
            $PreparedIpaToolSha256,
            $ExpectedIpaToolExecutableSha256,
            [System.StringComparison]::Ordinal
        )) {
            throw "Проверка staging: копия ipatool изменила байты."
        }

        $PreparedCommand = Join-Path $PreparedBin "apprestore.cmd"
        $PreparedVersionOutput = @(& $PreparedCommand --version 2>&1)
        $PreparedVersionCode = $LASTEXITCODE
        $PreparedVersion = ($PreparedVersionOutput -join "`n").Trim()
        if (
            $PreparedVersionCode -ne 0 -or
            -not [string]::Equals(
                $PreparedVersion,
                $AppRestoreVersion,
                [System.StringComparison]::Ordinal
            )
        ) {
            throw (
                "Проверка staging: ожидалась версия $AppRestoreVersion, " +
                "получено '$PreparedVersion' (код $PreparedVersionCode)."
            )
        }
    }

    $VerifyInstallation = {
        param([string]$LiveRoot)

        $LiveMarker = Join-Path $LiveRoot $ManagedInstallMarkerName
        $LiveCommand = Join-Path $LiveRoot "bin\apprestore.cmd"
        $LivePython = Join-Path $LiveRoot ".venv\Scripts\python.exe"
        foreach ($RequiredLiveFile in @($LiveMarker, $LiveCommand, $LivePython)) {
            if (-not (Test-Path -LiteralPath $RequiredLiveFile -PathType Leaf)) {
                throw "Проверка live-установки: отсутствует $RequiredLiveFile"
            }
        }
        if (-not [string]::Equals(
            [System.IO.File]::ReadAllText($LiveMarker),
            $ManagedInstallMarkerValue,
            [System.StringComparison]::Ordinal
        )) {
            throw "Проверка live-установки: marker AppRestore повреждён."
        }

        $LiveVersionOutput = @(& $LiveCommand --version 2>&1)
        $LiveVersionCode = $LASTEXITCODE
        $LiveVersion = ($LiveVersionOutput -join "`n").Trim()
        if (
            $LiveVersionCode -ne 0 -or
            -not [string]::Equals(
                $LiveVersion,
                $AppRestoreVersion,
                [System.StringComparison]::Ordinal
            )
        ) {
            throw (
                "Проверка live-установки: ожидалась версия $AppRestoreVersion, " +
                "получено '$LiveVersion' (код $LiveVersionCode)."
            )
        }
    }

    Invoke-AppRestoreInstallTransaction `
        -StagingRoot $StagingRoot `
        -InstallRoot $InstallRoot `
        -BackupRoot $BackupRoot `
        -ManagedMarkerName $ManagedInstallMarkerName `
        -ManagedMarkerValue $ManagedInstallMarkerValue `
        -PrepareStaging $PrepareStaging `
        -VerifyStaging $VerifyStaging `
        -VerifyInstallation $VerifyInstallation

    $BinTarget = Join-Path $InstallRoot "bin"

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
    if (Test-Path -LiteralPath $StagingRoot) {
        $ResolvedStagingRoot = [System.IO.Path]::GetFullPath($StagingRoot)
        $StagingParent = [System.IO.Path]::GetDirectoryName($ResolvedStagingRoot)
        $StagingLeaf = [System.IO.Path]::GetFileName($ResolvedStagingRoot)
        $StagingItem = Get-Item -LiteralPath $ResolvedStagingRoot -Force
        $StagingReparse = $null
        if ($StagingItem.PSIsContainer) {
            $StagingReparse = Get-ChildItem `
                -LiteralPath $ResolvedStagingRoot `
                -Force `
                -Recurse `
                -ErrorAction SilentlyContinue |
                Where-Object {
                    ($_.Attributes -band
                        [System.IO.FileAttributes]::ReparsePoint) -ne 0
                } |
                Select-Object -First 1
        }
        if (
            $StagingItem.PSIsContainer -and
            ($StagingItem.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -eq 0 -and
            $null -eq $StagingReparse -and
            [string]::Equals(
                $StagingParent,
                $ProgramsRoot,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -and
            $StagingLeaf.StartsWith(
                "AppRestore.staging-",
                [System.StringComparison]::Ordinal
            )
        ) {
            Remove-Item -LiteralPath $ResolvedStagingRoot -Recurse -Force
        }
        else {
            Write-Warning "Небезопасный staging оставлен для ручной проверки: $ResolvedStagingRoot"
        }
    }
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
