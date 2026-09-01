#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [switch]$PurgeUserData
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ManagedInstallMarkerName = ".apprestore-managed"
$ManagedInstallMarkerValue = "AppRestore managed installation v1"
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

function Assert-PlainDirectoryTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if (-not $Item.PSIsContainer) {
            throw "Отказ от удаления объекта, который не является каталогом: $Path"
        }
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Отказ от удаления ссылки или точки повторной обработки: $Path"
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
            throw "Отказ: каталог содержит ссылку: $($NestedReparse.FullName)"
        }
    }
}

function Assert-ManagedAppRestoreInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Assert-PlainDirectoryTree -Path $Path
    $MarkerPath = Join-Path $Path $ManagedInstallMarkerName
    $HasExactMarker = (
        (Test-Path -LiteralPath $MarkerPath -PathType Leaf) -and
        [string]::Equals(
            [System.IO.File]::ReadAllText($MarkerPath),
            $ManagedInstallMarkerValue,
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

$BinTarget = Join-Path $InstallRoot "bin"
$ProgramExists = Test-Path -LiteralPath $InstallRoot
if ($ProgramExists) {
    Assert-ManagedAppRestoreInstall -Path $InstallRoot
}
$RemoveProgram = $false
$UpdatePath = $false
if ($ProgramExists) {
    $RemoveProgram = $PSCmdlet.ShouldProcess($InstallRoot, "удалить AppRestore")
    $UpdatePath = $RemoveProgram
}
else {
    # После ручного удаления каталога запись PATH очищается отдельным действием.
    $RemoveProgram = $true
    $UpdatePath = $PSCmdlet.ShouldProcess(
        "пользовательский PATH",
        "убрать оставшийся каталог AppRestore\bin"
    )
}

if ($PurgeUserData) {
    $IpaTool = Join-Path $BinTarget "ipatool.exe"
    if (Test-Path -LiteralPath $IpaTool -PathType Leaf) {
        if ($PSCmdlet.ShouldProcess(
            "учётные данные ipatool",
            "отозвать локальную авторизацию Apple ID"
        )) {
            & $IpaTool auth revoke
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "ipatool не подтвердил отзыв авторизации; проверьте его хранилище вручную."
            }
        }
    }
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UpdatePath -and $null -ne $UserPath) {
    $BinForComparison = $BinTarget.TrimEnd("\")
    $KeptSegments = @(
        foreach ($Segment in ($UserPath -split ";")) {
            $CleanSegment = $Segment.Trim().Trim('"').TrimEnd("\")
            if (-not [string]::Equals(
                $CleanSegment,
                $BinForComparison,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                $Segment
            }
        }
    )
    $UpdatedUserPath = ($KeptSegments -join ";").Trim(";")
    if (-not [string]::Equals(
        $UpdatedUserPath,
        $UserPath,
        [System.StringComparison]::Ordinal
    )) {
        [Environment]::SetEnvironmentVariable("Path", $UpdatedUserPath, "User")
    }
}

if ($ProgramExists -and $RemoveProgram) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    Write-Host "AppRestore удалён. Откройте новый терминал, чтобы обновился PATH."
}
elseif ($ProgramExists) {
    Write-Host "Удаление AppRestore отменено; запись PATH сохранена."
}
else {
    Write-Host "AppRestore не установлен в $InstallRoot."
}

if ($PurgeUserData) {
    $DefaultIpaLibrary = [System.IO.Path]::GetFullPath(
        (Join-Path $HOME "AppRestore\ipas")
    )
    $DefaultCache = [System.IO.Path]::GetFullPath(
        (Join-Path $KnownLocalAppData "AppRestore")
    )
    # ipatool 2.4+ кэширует здесь SAP-рантайм авторизации (Unicorn).
    $IpaToolCache = [System.IO.Path]::GetFullPath(
        (Join-Path $KnownLocalAppData "ipatool")
    )
    foreach ($UserDataTarget in @($DefaultIpaLibrary, $DefaultCache, $IpaToolCache)) {
        Assert-PlainDirectoryTree -Path $UserDataTarget
        if (
            (Test-Path -LiteralPath $UserDataTarget) -and
            $PSCmdlet.ShouldProcess(
                $UserDataTarget,
                "безвозвратно удалить пользовательские данные AppRestore"
            )
        ) {
            Remove-Item -LiteralPath $UserDataTarget -Recurse -Force
        }
    }
    Write-Warning "Каталоги из APPRESTORE_IPA_DIR/APPRESTORE_CACHE_DIR автоматически не удаляются."
}
else {
    Write-Host "IPA, кэш AppRestore, SAP-рантайм и авторизация ipatool сохранены."
    Write-Host "Для их явного удаления используйте -PurgeUserData."
}
