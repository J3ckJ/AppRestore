#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [switch]$PurgeUserData
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "Переменная LOCALAPPDATA не определена."
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
    throw "Отказ: цель удаления не совпадает с фиксированным каталогом AppRestore."
}

function Assert-NotReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Отказ от удаления ссылки или точки повторной обработки: $Path"
        }
    }
}

Assert-NotReparsePoint -Path $InstallRoot
$BinTarget = Join-Path $InstallRoot "bin"
$ProgramExists = Test-Path -LiteralPath $InstallRoot
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
        (Join-Path $env:LOCALAPPDATA "AppRestore")
    )
    foreach ($UserDataTarget in @($DefaultIpaLibrary, $DefaultCache)) {
        Assert-NotReparsePoint -Path $UserDataTarget
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
    Write-Host "IPA, кэш и авторизация ipatool сохранены."
    Write-Host "Для их явного удаления используйте -PurgeUserData."
}
