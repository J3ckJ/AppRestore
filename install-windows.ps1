#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$NoPathUpdate,
    [switch]$SkipAppleBridge
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$AppRestoreVersion = "0.2.0"
$ManagedInstallMarkerName = ".apprestore-managed"
$ManagedInstallMarkerValue = "AppRestore managed installation v1"
$IpaToolVersion = "2.3.2"
$IpaToolSha256 = "6352441f6f91df7947aaa203b19cb7d3c9d77920fc466dd784ff9cae88db5c92"
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

function Assert-AppRestorePlainTree {
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

function Assert-AppRestoreManagedInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerName,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerValue
    )

    Assert-AppRestorePlainTree -Path $Path -Label "Установка AppRestore"
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

function Assert-AppRestoreRecoverableInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerName,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerValue
    )

    Assert-AppRestoreManagedInstall `
        -Path $Path `
        -ManagedMarkerName $ManagedMarkerName `
        -ManagedMarkerValue $ManagedMarkerValue
    foreach ($RequiredFile in @(
        (Join-Path $Path "bin\apprestore.cmd"),
        (Join-Path $Path ".venv\Scripts\python.exe")
    )) {
        if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
            throw "$Label неполон: отсутствует $RequiredFile"
        }
        $RequiredItem = Get-Item -LiteralPath $RequiredFile -Force
        if (
            ($RequiredItem.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label содержит ссылку: $RequiredFile"
        }
    }
}

function Invoke-AppRestoreBackupRecovery {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerName,
        [Parameter(Mandatory = $true)]
        [string]$ManagedMarkerValue
    )

    $ResolvedInstall = [System.IO.Path]::GetFullPath($InstallRoot)
    $ResolvedPrograms = [System.IO.Path]::GetDirectoryName($ResolvedInstall)
    $InstallLeaf = [System.IO.Path]::GetFileName($ResolvedInstall)
    if (-not [string]::Equals(
        $InstallLeaf,
        "AppRestore",
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Некорректное имя live-каталога AppRestore."
    }
    $ProgramsItem = Get-Item -LiteralPath $ResolvedPrograms -Force
    if (
        -not $ProgramsItem.PSIsContainer -or
        ($ProgramsItem.Attributes -band
            [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Каталог программ AppRestore небезопасен: $ResolvedPrograms"
    }

    $ExistingBackups = @(
        Get-ChildItem `
            -LiteralPath $ResolvedPrograms `
            -Force `
            -ErrorAction Stop |
            Where-Object {
                $_.Name.StartsWith(
                    "AppRestore.backup-",
                    [System.StringComparison]::Ordinal
                )
            }
    )
    $LiveExists = Test-Path -LiteralPath $ResolvedInstall
    if ($LiveExists) {
        Assert-AppRestoreRecoverableInstall `
            -Path $ResolvedInstall `
            -Label "Текущая установка" `
            -ManagedMarkerName $ManagedMarkerName `
            -ManagedMarkerValue $ManagedMarkerValue
        if ($ExistingBackups.Count -ne 0) {
            throw (
                "Найдены backup-каталоги при существующей live-установке. " +
                "Автоматическая установка остановлена, чтобы не создать " +
                "неоднозначное восстановление: " +
                (($ExistingBackups | ForEach-Object { $_.FullName }) -join ", ")
            )
        }
        return
    }
    if ($ExistingBackups.Count -gt 1) {
        throw (
            "Live-установка отсутствует, но найдено несколько backup-кандидатов. " +
            "Автовосстановление неоднозначно: " +
            (($ExistingBackups | ForEach-Object { $_.FullName }) -join ", ")
        )
    }
    if ($ExistingBackups.Count -eq 0) {
        return
    }

    $RecoveryCandidate = [System.IO.Path]::GetFullPath(
        $ExistingBackups[0].FullName
    )
    try {
        Assert-AppRestoreRecoverableInstall `
            -Path $RecoveryCandidate `
            -Label "Backup AppRestore" `
            -ManagedMarkerName $ManagedMarkerName `
            -ManagedMarkerValue $ManagedMarkerValue
    }
    catch {
        throw (
            "Live-установка отсутствует, а единственный backup " +
            "не прошёл безопасную проверку: $RecoveryCandidate. " +
            $_.Exception.Message
        )
    }
    Move-Item `
        -LiteralPath $RecoveryCandidate `
        -Destination $ResolvedInstall
    Assert-AppRestoreRecoverableInstall `
        -Path $ResolvedInstall `
        -Label "Восстановленная установка" `
        -ManagedMarkerName $ManagedMarkerName `
        -ManagedMarkerValue $ManagedMarkerValue
    Write-Host "Восстановлена прерванная установка AppRestore: $ResolvedInstall"
}

function Find-TrustedWinget {
    # winget is security-sensitive here because it downloads and launches
    # installers.  Never resolve it through PATH: the current directory or an
    # AppRestore bin directory could otherwise shadow the genuine executable.
    $RegistryBase = $null
    $CurrentVersionKey = $null
    try {
        $RegistryView = [Microsoft.Win32.RegistryView]::Default
        if ([Environment]::Is64BitOperatingSystem) {
            $RegistryView = [Microsoft.Win32.RegistryView]::Registry64
        }
        $RegistryBase = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
            [Microsoft.Win32.RegistryHive]::LocalMachine,
            $RegistryView
        )
        $CurrentVersionKey = $RegistryBase.OpenSubKey(
            "SOFTWARE\Microsoft\Windows\CurrentVersion",
            $false
        )
        if ($null -eq $CurrentVersionKey) {
            return $null
        }
        $ProgramFilesRoot = [string]$CurrentVersionKey.GetValue(
            "ProgramFilesDir",
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
    }
    catch {
        return $null
    }
    finally {
        if ($null -ne $CurrentVersionKey) {
            $CurrentVersionKey.Dispose()
        }
        if ($null -ne $RegistryBase) {
            $RegistryBase.Dispose()
        }
    }

    if ([string]::IsNullOrWhiteSpace($ProgramFilesRoot)) {
        return $null
    }
    $WindowsAppsRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $ProgramFilesRoot "WindowsApps")
    )
    if (-not (Test-Path -LiteralPath $WindowsAppsRoot -PathType Container)) {
        return $null
    }
    $WindowsAppsItem = Get-Item -LiteralPath $WindowsAppsRoot -Force
    if (
        ($WindowsAppsItem.Attributes -band
            [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        return $null
    }

    $ExpectedPublisher = (
        "CN=Microsoft Corporation, O=Microsoft Corporation, " +
        "L=Redmond, S=Washington, C=US"
    )
    $TrustedCandidates = @()
    $PackageNames = @()
    $PackageRepositoryBase = $null
    $PackageRepository = $null
    try {
        # Enumerating Program Files\WindowsApps is denied in Windows PowerShell
        # 5.1 on some supported systems.  The per-user package repository gives
        # us only registered package names; every resulting path is still
        # constrained to WindowsApps and verified below.
        $PackageRepositoryBase = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
            [Microsoft.Win32.RegistryHive]::CurrentUser,
            [Microsoft.Win32.RegistryView]::Default
        )
        $PackageRepository = $PackageRepositoryBase.OpenSubKey(
            (
                "Software\Classes\Local Settings\Software\Microsoft\Windows\" +
                "CurrentVersion\AppModel\Repository\Packages"
            ),
            $false
        )
        if ($null -ne $PackageRepository) {
            $PackageNames = @(
                $PackageRepository.GetSubKeyNames() |
                    Where-Object {
                        $_ -match (
                            "^Microsoft\.DesktopAppInstaller_" +
                            "[A-Za-z0-9._~-]+__8wekyb3d8bbwe$"
                        )
                    }
            )
        }
    }
    catch {
        $PackageNames = @()
    }
    finally {
        if ($null -ne $PackageRepository) {
            $PackageRepository.Dispose()
        }
        if ($null -ne $PackageRepositoryBase) {
            $PackageRepositoryBase.Dispose()
        }
    }

    foreach ($PackageName in $PackageNames) {
        $PackagePath = [System.IO.Path]::GetFullPath(
            (Join-Path $WindowsAppsRoot $PackageName)
        )
        if (-not [string]::Equals(
            [System.IO.Path]::GetDirectoryName($PackagePath),
            $WindowsAppsRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            continue
        }
        if (-not (Test-Path -LiteralPath $PackagePath -PathType Container)) {
            continue
        }
        $Package = Get-Item -LiteralPath $PackagePath -Force
        if (
            ($Package.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            continue
        }
        $ManifestPath = Join-Path $Package.FullName "AppxManifest.xml"
        $Candidate = Join-Path $Package.FullName "winget.exe"
        if (
            -not (Test-Path -LiteralPath $ManifestPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $Candidate -PathType Leaf)
        ) {
            continue
        }
        try {
            [xml]$Manifest = Get-Content -LiteralPath $ManifestPath -Raw
            $Identity = $Manifest.Package.Identity
            if (
                -not [string]::Equals(
                    [string]$Identity.Name,
                    "Microsoft.DesktopAppInstaller",
                    [System.StringComparison]::Ordinal
                ) -or
                -not [string]::Equals(
                    [string]$Identity.Publisher,
                    $ExpectedPublisher,
                    [System.StringComparison]::Ordinal
                )
            ) {
                continue
            }
            $CandidateItem = Get-Item -LiteralPath $Candidate -Force
            if (
                ($CandidateItem.Attributes -band
                    [System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                continue
            }
            $Signature = (
                Microsoft.PowerShell.Security\Get-AuthenticodeSignature `
                    -LiteralPath $Candidate
            )
            if (
                $Signature.Status -ne
                    [System.Management.Automation.SignatureStatus]::Valid -or
                $null -eq $Signature.SignerCertificate -or
                -not [string]::Equals(
                    $Signature.SignerCertificate.Subject,
                    $ExpectedPublisher,
                    [System.StringComparison]::Ordinal
                )
            ) {
                continue
            }
            $TrustedCandidates += [pscustomobject]@{
                Version = [version]([string]$Identity.Version)
                Executable = $CandidateItem.FullName
            }
        }
        catch {
            continue
        }
    }

    $Selected = $TrustedCandidates |
        Sort-Object -Property Version -Descending |
        Select-Object -First 1
    if ($null -eq $Selected) {
        return $null
    }
    return [string]$Selected.Executable
}

function Find-CompatiblePython {
    $VersionCheck = (
        "import struct, sys; " +
        "raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 14) " +
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

    $Winget = Find-TrustedWinget
    if ($null -ne $Winget) {
        Write-Host "Python 3.10–3.13 не найден. Установка Python 3.12 для текущего пользователя…"
        & $Winget install `
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

$InstallerIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$InstallerUserSid = $InstallerIdentity.User
if ($null -eq $InstallerUserSid) {
    throw "Не удалось определить SID текущего пользователя."
}
$InstallerMutexName = "Local\AppRestore.Install." + $InstallerUserSid.Value
$InstallerMutex = [System.Threading.Mutex]::new($false, $InstallerMutexName)
$InstallerMutexAcquired = $false
try {
    try {
        $InstallerMutexAcquired = $InstallerMutex.WaitOne(
            [TimeSpan]::FromMinutes(15)
        )
    }
    catch [System.Threading.AbandonedMutexException] {
        # A crashed installer releases the kernel object as abandoned. We own
        # it now; recover a unique managed backup before any prerequisite work.
        $InstallerMutexAcquired = $true
    }
    if (-not $InstallerMutexAcquired) {
        throw "Другая установка AppRestore не завершилась за 15 минут."
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

Invoke-AppRestoreBackupRecovery `
    -InstallRoot $InstallRoot `
    -ManagedMarkerName $ManagedInstallMarkerName `
    -ManagedMarkerValue $ManagedInstallMarkerValue

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

function Start-AppleServicesAndWait {
    param(
        [Parameter(Mandatory = $true)]
        [int]$WaitSeconds
    )

    $FoundService = $false
    foreach ($ServiceName in @("Apple Mobile Device Service", "Apple Mobile Device")) {
        $Service = (
            Microsoft.PowerShell.Management\Get-Service `
                -Name $ServiceName `
                -ErrorAction SilentlyContinue
        )
        if ($null -eq $Service) {
            continue
        }
        $FoundService = $true
        if ($Service.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
            Write-Host "Запуск службы $ServiceName…"
            try {
                Microsoft.PowerShell.Management\Start-Service `
                    -InputObject $Service `
                    -ErrorAction Stop
            }
            catch {
                Write-Warning (
                    "Служба $ServiceName не запустилась без повышения прав: " +
                    "$($_.Exception.Message)"
                )
            }
        }
    }
    if (-not $FoundService) {
        return $false
    }

    for ($Index = 0; $Index -lt $WaitSeconds; $Index++) {
        if (Test-AppleUsbmuxPort) {
            Write-Host "Apple USB-мост готов."
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Ensure-AppleBridge {
    if (Test-AppleUsbmuxPort) {
        Write-Host "Apple USB-мост уже доступен (127.0.0.1:27015)."
        return
    }

    if (Start-AppleServicesAndWait -WaitSeconds 20) {
        return
    }

    $Winget = Find-TrustedWinget
    if ($null -eq $Winget) {
        Write-Warning (
            "Подписанный winget из Microsoft Desktop App Installer не найден. " +
            "Установите Apple Devices/iTunes или Apple Mobile Device Support, " +
            "затем выполните: apprestore setup"
        )
        return
    }

    Write-Host "Установка Apple Mobile Device Support через проверенный winget…"
    & $Winget install `
        -e `
        --id Apple.AppleMobileDeviceSupport `
        --accept-package-agreements `
        --accept-source-agreements `
        --disable-interactivity
    $WingetCode = $LASTEXITCODE
    # 0 = OK; распространённые коды «уже установлено».
    if ($WingetCode -notin @(0, -1978335189, -1978334964)) {
        Write-Warning (
            "Не удалось поставить Apple.AppleMobileDeviceSupport автоматически " +
            "без повышения прав (код $WingetCode). AppRestore уже установлен; " +
            "после установки Apple Devices/iTunes выполните: apprestore setup"
        )
        return
    }

    if (Start-AppleServicesAndWait -WaitSeconds 45) {
        return
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
        [scriptblock]$VerifyInstallation,
        [switch]$InstallMutexAlreadyHeld,
        [switch]$BackupRecoveryAlreadyPerformed,
        [ValidateRange(1, 3600)]
        [int]$MutexTimeoutSeconds = 900
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
    if ($BackupRecoveryAlreadyPerformed -and -not $InstallMutexAlreadyHeld) {
        throw (
            "Пропустить backup recovery можно только при уже захваченном " +
            "mutex установки AppRestore."
        )
    }

    $TransactionMutex = $null
    $TransactionMutexAcquired = $false
    if (-not $InstallMutexAlreadyHeld) {
        $CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $CurrentUserSid = $CurrentIdentity.User
        if ($null -eq $CurrentUserSid) {
            throw "Не удалось определить SID текущего пользователя."
        }
        $TransactionMutexName = (
            "Local\AppRestore.Install." + $CurrentUserSid.Value
        )
        $TransactionMutex = [System.Threading.Mutex]::new(
            $false,
            $TransactionMutexName
        )
        try {
            try {
                $TransactionMutexAcquired = $TransactionMutex.WaitOne(
                    [TimeSpan]::FromSeconds($MutexTimeoutSeconds)
                )
            }
            catch [System.Threading.AbandonedMutexException] {
                # The previous installer crashed while owning the mutex. The
                # current thread owns it now and must run backup recovery.
                $TransactionMutexAcquired = $true
            }
            if (-not $TransactionMutexAcquired) {
                throw (
                    "Другая установка AppRestore не завершилась за " +
                    "$MutexTimeoutSeconds секунд."
                )
            }
        }
        catch {
            $TransactionMutex.Dispose()
            $TransactionMutex = $null
            throw
        }
    }

    try {
    if (-not (Test-Path -LiteralPath $ResolvedStaging -PathType Container)) {
        throw "Не найден подготовленный staging-каталог AppRestore."
    }

    $RemovePlainTree = {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Path,
            [Parameter(Mandatory = $true)]
            [string]$Label
        )
        if (Test-Path -LiteralPath $Path) {
            Assert-AppRestorePlainTree -Path $Path -Label $Label
            Remove-Item -LiteralPath $Path -Recurse -Force
        }
    }

    $BackupCreated = $false
    $InstallCommitted = $false
    $Success = $false
    try {
        if (-not $BackupRecoveryAlreadyPerformed) {
            Invoke-AppRestoreBackupRecovery `
                -InstallRoot $ResolvedInstall `
                -ManagedMarkerName $ManagedMarkerName `
                -ManagedMarkerValue $ManagedMarkerValue
        }
        Assert-AppRestorePlainTree `
            -Path $ResolvedStaging `
            -Label "Staging AppRestore"
        & $PrepareStaging $ResolvedStaging
        Assert-AppRestorePlainTree `
            -Path $ResolvedStaging `
            -Label "Staging AppRestore"
        & $VerifyStaging $ResolvedStaging

        if (Test-Path -LiteralPath $ResolvedInstall) {
            Assert-AppRestoreRecoverableInstall `
                -Path $ResolvedInstall `
                -Label "Текущая установка" `
                -ManagedMarkerName $ManagedMarkerName `
                -ManagedMarkerValue $ManagedMarkerValue
            if (Test-Path -LiteralPath $ResolvedBackup) {
                throw "Backup-каталог AppRestore уже существует: $ResolvedBackup"
            }
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
    finally {
        if ($TransactionMutexAcquired -and $null -ne $TransactionMutex) {
            $TransactionMutex.ReleaseMutex()
            $TransactionMutexAcquired = $false
        }
        if ($null -ne $TransactionMutex) {
            $TransactionMutex.Dispose()
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
    $RequirementsTarget = Join-Path $SourceTarget "requirements"
    $WheelsTarget = Join-Path $RequirementsTarget "wheels"
    $BinTarget = Join-Path $StagingRoot "bin"
    New-Item -ItemType Directory -Path $CoreTarget -Force | Out-Null
    New-Item -ItemType Directory -Path $WheelsTarget -Force | Out-Null
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
    foreach ($RequirementFile in @(
        "README.md",
        "build.in",
        "build.lock",
        "runtime.in",
        "runtime.lock"
    )) {
        Copy-Item `
            -LiteralPath (Join-Path $PSScriptRoot "requirements\$RequirementFile") `
            -Destination (Join-Path $RequirementsTarget $RequirementFile) `
            -Force
    }
    Copy-Item `
        -LiteralPath (
            Join-Path $PSScriptRoot `
                "requirements\wheels\hexdump-3.3-py3-none-any.whl"
        ) `
        -Destination $WheelsTarget `
        -Force

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
        $PreparedRequirements = Join-Path $PreparedSource "requirements"
        $PreparedWheels = Join-Path $PreparedRequirements "wheels"
        & $PreparedVenvPython `
            -m pip install `
            --disable-pip-version-check `
            --no-input `
            --require-hashes `
            --only-binary=:all: `
            --no-deps `
            --find-links $PreparedWheels `
            --requirement (Join-Path $PreparedRequirements "build.lock")
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить hash-locked build-зависимости."
        }
        & $PreparedVenvPython `
            -m pip install `
            --disable-pip-version-check `
            --no-input `
            --require-hashes `
            --only-binary=:all: `
            --no-deps `
            --find-links $PreparedWheels `
            --requirement (Join-Path $PreparedRequirements "runtime.lock")
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить hash-locked runtime-зависимости."
        }
        & $PreparedVenvPython `
            -m pip install `
            --disable-pip-version-check `
            --no-input `
            --no-index `
            --no-deps `
            --no-build-isolation `
            --force-reinstall `
            $PreparedSource
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить локальный AppRestore."
        }

        $CommandWrapper = @'
@echo off
setlocal
set "PATH=%~dp0;%PATH%"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONSTARTUP="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0..\.venv\Scripts\python.exe" -X utf8 -I -m apprestore_core.cli %*
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
            (Join-Path $PreparedSource "requirements\build.lock"),
            (Join-Path $PreparedSource "requirements\runtime.lock"),
            (
                Join-Path $PreparedSource `
                    "requirements\wheels\hexdump-3.3-py3-none-any.whl"
            ),
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
        -VerifyInstallation $VerifyInstallation `
        -InstallMutexAlreadyHeld `
        -BackupRecoveryAlreadyPerformed

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
        $StagingInspectionFailed = $false
        if ($StagingItem.PSIsContainer) {
            try {
                $StagingReparse = Get-ChildItem `
                    -LiteralPath $ResolvedStagingRoot `
                    -Force `
                    -Recurse `
                    -ErrorAction Stop |
                    Where-Object {
                        ($_.Attributes -band
                            [System.IO.FileAttributes]::ReparsePoint) -ne 0
                    } |
                    Select-Object -First 1
            }
            catch {
                $StagingInspectionFailed = $true
            }
        }
        if (
            $StagingItem.PSIsContainer -and
            ($StagingItem.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -eq 0 -and
            $null -eq $StagingReparse -and
            -not $StagingInspectionFailed -and
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
        $ResolvedTempRoot = [System.IO.Path]::GetFullPath($TempRoot)
        $SystemTempRoot = [System.IO.Path]::GetFullPath(
            [System.IO.Path]::GetTempPath()
        ).TrimEnd("\")
        $TempParent = [System.IO.Path]::GetDirectoryName($ResolvedTempRoot)
        $TempLeaf = [System.IO.Path]::GetFileName($ResolvedTempRoot)
        $ExpectedTempPrefix = "AppRestore-install-"
        $TempSuffix = ""
        if ($TempLeaf.StartsWith(
            $ExpectedTempPrefix,
            [System.StringComparison]::Ordinal
        )) {
            $TempSuffix = $TempLeaf.Substring($ExpectedTempPrefix.Length)
        }
        try {
            $TempItem = Get-Item -LiteralPath $ResolvedTempRoot -Force
            $NestedTempReparse = $null
            if ($TempItem.PSIsContainer) {
                $NestedTempReparse = Get-ChildItem `
                    -LiteralPath $ResolvedTempRoot `
                    -Force `
                    -Recurse `
                    -ErrorAction Stop |
                    Where-Object {
                        ($_.Attributes -band
                            [System.IO.FileAttributes]::ReparsePoint) -ne 0
                    } |
                    Select-Object -First 1
            }
            if (
                $TempItem.PSIsContainer -and
                ($TempItem.Attributes -band
                    [System.IO.FileAttributes]::ReparsePoint) -eq 0 -and
                $null -eq $NestedTempReparse -and
                [string]::Equals(
                    $TempParent,
                    $SystemTempRoot,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -and
                $TempLeaf.StartsWith(
                    $ExpectedTempPrefix,
                    [System.StringComparison]::Ordinal
                ) -and
                $TempSuffix -match "^[0-9a-fA-F]{32}$"
            ) {
                Remove-Item -LiteralPath $ResolvedTempRoot -Recurse -Force
            }
            else {
                Write-Warning (
                    "Небезопасный temp-каталог оставлен для ручной проверки: " +
                    $ResolvedTempRoot
                )
            }
        }
        catch {
            Write-Warning (
                "Temp-каталог AppRestore не удалён после ошибки безопасной " +
                "проверки: $ResolvedTempRoot. $($_.Exception.Message)"
            )
        }
    }
}
}
finally {
    if ($InstallerMutexAcquired) {
        $InstallerMutex.ReleaseMutex()
        $InstallerMutexAcquired = $false
    }
    $InstallerMutex.Dispose()
}
