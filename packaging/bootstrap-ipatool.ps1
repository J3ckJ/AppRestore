<#
.SYNOPSIS
  Build AppRestore's patched ipatool for running from source on Windows.

.DESCRIPTION
  Same steps as packaging/bootstrap-ipatool.sh, in plain Windows PowerShell 5.1+
  (no git, bash, tar or other third-party tools):
    1. finds Go 1.25.0 (on PATH or in build\ipatool-bootstrap\), or downloads the
       official go1.25.0 zip from go.dev into build\ipatool-bootstrap\ and checks
       its SHA-256 against the values pinned below. The system Go is not touched.
    2. downloads majd/ipatool at the commit pinned in packaging/build-ipatool.sh
       (zip from GitHub) and checks a SHA-256 of the whole source tree;
    3. checks every patch 0001-0003 against the SHA-256 pinned in
       build-ipatool.sh, applies them strictly (exact context, no fuzz);
    4. builds windows-amd64 with the same flags as build-ipatool.sh
       (CGO_ENABLED=0, -trimpath, -buildvcs=false, version 2.6.0);
    5. checks the patch markers, installs bin\ipatool.exe (resolve_tool() looks
       there first) and prints its SHA-256.
  Idempotent: a second run with the same inputs keeps the existing binary.
  No secrets, no account access.

  Status: logic tested with PowerShell 7 on Linux (cross-build windows-amd64,
  byte-identical to build-ipatool.sh windows-amd64). Not run on real Windows.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\bootstrap-ipatool.ps1
#>
[CmdletBinding()]
param(
  [switch]$Force,
  [string]$Repo = '',
  # Use an existing Go 1.25.0 installation (folder that contains bin\go[.exe]).
  [string]$GoRoot = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar in 5.1

$GoVersion = 'go1.25.0'
# Official go.dev SHA-256 of the go1.25.0 Windows archives.
$GoZipSha256 = @{
  'windows-amd64' = '89efb4f9b30812eee083cc1770fdd2913c14d301064f6454851428f9707d190b'
  'windows-arm64' = '27bab004c72b3d7bd05a69b6ec0fc54a309b4b78cc569dd963d8b3ec28bfdb8c'
}
# SHA-256 of the source manifest of majd/ipatool cde7d00 (one line per file:
# "<sha256>  <path>\n", paths with '/', ordinal order). Computed from a git
# checkout of the commit; GitHub's zip of the commit gives the same tree.
$SourceTreeSha256 = @{
  'cde7d00355e152714377b953ec57438626d3cb5a' = 'fd522fbface3b29366d01205ba21049c4e54bec3a3c74afede14c78363189a97'
}
# Strings that exist in the binary only with the given patch (the same strings
# build-ipatool.sh checks).
$PatchMarkers = [ordered]@{
  'redirect' = 'too many authentication redirects'
  '0001'     = 'appstore.CountryCodeFromStoreFront'
  '0002'     = '--all cannot be combined with --page or --max-results'
  '0003'     = 'keychain-passphrase-stdin'
}
$Asset = 'windows-amd64'

function Say([string]$Text) { Write-Host "==> $Text" }
function Fail([string]$Text) { throw "bootstrap-ipatool: $Text" }

function Get-Sha256([string]$Path) {
  $stream = [System.IO.File]::OpenRead($Path)
  try {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return (-join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString('x2') })) }
    finally { $sha.Dispose() }
  } finally { $stream.Dispose() }
}

function Get-StringSha256([string]$Text) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
    return (-join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }))
  } finally { $sha.Dispose() }
}

function Invoke-Download([string]$Url, [string]$OutFile) {
  # TLS 1.2 is not on by default in Windows PowerShell 5.1 on older systems.
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  } catch { }
  if (-not $Url.StartsWith('https://')) { Fail "refusing non-https URL $Url" }
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try { Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing; return }
    catch { if ($attempt -eq 3) { throw } ; Start-Sleep -Seconds (2 * $attempt) }
  }
}

function Expand-Zip([string]$Zip, [string]$Dest) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Dest)
}

function Get-GoVersionOf([string]$GoExe) {
  if (-not (Test-Path -LiteralPath $GoExe -PathType Leaf)) { return '' }
  $saved = $env:GOTOOLCHAIN
  try {
    $env:GOTOOLCHAIN = 'local'
    $v = Invoke-NativeText $GoExe @('env', 'GOVERSION')
    if ($LASTEXITCODE -ne 0) { return '' }
    return $v.Trim()
  } catch { return '' } finally { $env:GOTOOLCHAIN = $saved }
}

# Windows PowerShell 5.1 turns redirected native stderr into error records,
# which $ErrorActionPreference = 'Stop' would throw on: run natives with Continue.
function Invoke-NativeText([string]$Exe, [string[]]$Arguments) {
  $saved = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    return ((& $Exe @Arguments 2>&1 | ForEach-Object { "$_" }) -join "`n")
  } finally { $ErrorActionPreference = $saved }
}

function Test-IsWindowsHost {
  return ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT)
}

# Reads the pinned commit, version and patch list from build-ipatool.sh so the
# two build paths can never drift apart.
function Read-BuildPins([string]$BuildScript) {
  $text = [System.IO.File]::ReadAllText($BuildScript)
  $commit = [regex]::Match($text, '(?m)^commit="([0-9a-f]{40})"$')
  $version = [regex]::Match($text, '(?m)^version="([0-9.]+)"$')
  if (-not $commit.Success -or -not $version.Success) { Fail "cannot read commit/version from $BuildScript" }
  $patches = @()
  foreach ($m in [regex]::Matches($text, '"(\d{4}-[A-Za-z0-9._-]+\.patch):([0-9a-f]{64})"')) {
    $patches += [pscustomobject]@{ Name = $m.Groups[1].Value; Sha256 = $m.Groups[2].Value }
  }
  if ($patches.Count -lt 3) { Fail "expected patches 0001-0003 in $BuildScript, found $($patches.Count)" }
  return [pscustomobject]@{ Commit = $commit.Groups[1].Value; Version = $version.Groups[1].Value; Patches = $patches }
}

function Get-TreeSha256([string]$Dir) {
  $base = (Resolve-Path -LiteralPath $Dir).Path.TrimEnd('\', '/')
  $rel = New-Object System.Collections.Generic.List[string]
  foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -Force -File) {
    $rel.Add($f.FullName.Substring($base.Length + 1).Replace('\', '/'))
  }
  $rel.Sort([System.StringComparer]::Ordinal)
  $sb = New-Object System.Text.StringBuilder
  foreach ($r in $rel) {
    $null = $sb.Append((Get-Sha256 (Join-Path $base $r))).Append('  ').Append($r).Append("`n")
  }
  return (Get-StringSha256 $sb.ToString())
}

# Strict applier for git-style unified diffs (modified and new text files only;
# exact positions and context, no fuzz, no offset) - the subset our patches use.
# Throws without writing anything if any hunk does not match.
function Invoke-ApplyPatch([string]$SrcDir, [string]$PatchFile) {
  $lines = [System.IO.File]::ReadAllText($PatchFile).Split("`n")
  $files = @()
  $i = 0
  while ($i -lt $lines.Length) {
    $line = $lines[$i]
    if ($line -match '^(deleted file mode|rename from|rename to|copy from|old mode|new mode|similarity index|GIT binary patch|Binary files)') {
      Fail "$([IO.Path]::GetFileName($PatchFile)): unsupported patch feature: $line"
    }
    if ($line.StartsWith('--- ') -and ($i + 1) -lt $lines.Length -and $lines[$i + 1].StartsWith('+++ ')) {
      $old = $line.Substring(4); $new = $lines[$i + 1].Substring(4)
      $isNew = ($old -eq '/dev/null')
      if ($new -eq '/dev/null') { Fail "file deletion is not supported" }
      if (-not $new.StartsWith('b/')) { Fail "unexpected path $new" }
      $path = $new.Substring(2)
      if ($path -match '(^|/)\.\.(/|$)' -or $path.StartsWith('/') -or $path -match ':') { Fail "unsafe path $path" }
      $i += 2
      $hunks = @()
      while ($i -lt $lines.Length -and $lines[$i].StartsWith('@@')) {
        $h = [regex]::Match($lines[$i], '^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
        if (-not $h.Success) { Fail "bad hunk header: $($lines[$i])" }
        $oldStart = [int]$h.Groups[1].Value
        $oldCount = if ($h.Groups[2].Success) { [int]$h.Groups[2].Value } else { 1 }
        $newCount = if ($h.Groups[4].Success) { [int]$h.Groups[4].Value } else { 1 }
        $i++
        $body = @(); $seenOld = 0; $seenNew = 0
        while ($seenOld -lt $oldCount -or $seenNew -lt $newCount) {
          if ($i -ge $lines.Length) { Fail "truncated hunk in $path" }
          $b = $lines[$i]
          if ($b.StartsWith('\')) { Fail "'No newline at end of file' is not supported ($path)" }
          $tag = if ($b.Length -gt 0) { $b[0] } else { ' ' }   # an empty line is empty context
          $text = if ($b.Length -gt 0) { $b.Substring(1) } else { '' }
          switch ($tag) {
            ' ' { $seenOld++; $seenNew++ }
            '-' { $seenOld++ }
            '+' { $seenNew++ }
            default { Fail "bad hunk line in ${path}: $b" }
          }
          $body += ,@([string]$tag, $text)
          $i++
        }
        if ($seenOld -ne $oldCount -or $seenNew -ne $newCount) { Fail "hunk line counts do not match in $path" }
        $hunks += ,@($oldStart, $oldCount, $body)
      }
      if ($hunks.Count -eq 0) { Fail "no hunks for $path" }
      $files += ,@($path, $isNew, $hunks)
      continue
    }
    $i++
  }
  if ($files.Count -eq 0) { Fail "no file changes in $PatchFile" }

  # Compute every result first, then write: a failing patch leaves the tree as it was.
  $results = @()
  foreach ($f in $files) {
    $path = $f[0]; $isNew = $f[1]; $hunks = $f[2]
    $full = Join-Path $SrcDir ($path.Replace('/', [IO.Path]::DirectorySeparatorChar))
    if ($isNew) {
      if (Test-Path -LiteralPath $full) { Fail "$path already exists" }
      $src = @()
    } else {
      if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { Fail "$path is missing" }
      $content = [System.IO.File]::ReadAllText($full)
      if ($content.Contains("`r")) { Fail "$path has CR line endings" }
      if (-not $content.EndsWith("`n")) { Fail "$path does not end with a newline" }
      $src = $content.Substring(0, $content.Length - 1).Split("`n")
    }
    $out = New-Object System.Collections.Generic.List[string]
    $pos = 0   # 0-based index into $src
    foreach ($h in $hunks) {
      $start = if ($h[1] -eq 0) { $h[0] } else { $h[0] - 1 }
      if ($start -lt $pos -or $start -gt $src.Length) { Fail "hunk out of range in $path" }
      for ($k = $pos; $k -lt $start; $k++) { $out.Add($src[$k]) }
      $pos = $start
      foreach ($entry in $h[2]) {
        $tag = $entry[0]; $text = $entry[1]
        if ($tag -eq ' ' -or $tag -eq '-') {
          if ($pos -ge $src.Length -or $src[$pos] -cne $text) { Fail "patch does not apply to $path at line $($pos + 1)" }
          if ($tag -eq ' ') { $out.Add($text) }
          $pos++
        } else { $out.Add($text) }
      }
    }
    for ($k = $pos; $k -lt $src.Length; $k++) { $out.Add($src[$k]) }
    $results += ,@($full, (($out -join "`n") + "`n"))
  }
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  foreach ($r in $results) {
    $dir = Split-Path -Parent $r[0]
    if (-not (Test-Path -LiteralPath $dir)) { $null = New-Item -ItemType Directory -Path $dir }
    [System.IO.File]::WriteAllText($r[0], $r[1], $utf8)
  }
}

function Test-BinaryMarker([string]$Binary, [string]$Marker) {
  $latin1 = [System.Text.Encoding]::GetEncoding(28591)
  $text = $latin1.GetString([System.IO.File]::ReadAllBytes($Binary))
  return ($text.IndexOf($Marker, [System.StringComparison]::Ordinal) -ge 0)
}

function Test-AllMarkers([string]$Binary) {
  $latin1 = [System.Text.Encoding]::GetEncoding(28591)
  $text = $latin1.GetString([System.IO.File]::ReadAllBytes($Binary))
  foreach ($m in $PatchMarkers.Values) {
    if ($text.IndexOf($m, [System.StringComparison]::Ordinal) -lt 0) { return $false }
  }
  return $true
}

function Invoke-Main {
  if ($PSVersionTable.PSVersion.Major -lt 5) { Fail 'PowerShell 5 or newer is required' }
  $root = if ($Repo) { (Resolve-Path -LiteralPath $Repo).Path } else { (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
  $buildScript = Join-Path $root 'packaging/build-ipatool.sh'
  if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    Fail "no packaging/build-ipatool.sh under $root (run from the AppRestore checkout or pass -Repo)"
  }
  $pins = Read-BuildPins $buildScript
  if (-not $SourceTreeSha256.ContainsKey($pins.Commit)) { Fail "no pinned source tree SHA-256 for commit $($pins.Commit)" }

  $state = Join-Path $root 'build/ipatool-bootstrap'
  $null = New-Item -ItemType Directory -Force -Path $state
  $exe = if (Test-IsWindowsHost) { '.exe' } else { '' }

  # --- Go 1.25.0 ---------------------------------------------------------
  $goExe = ''
  if ($GoRoot) {
    $candidate = Join-Path $GoRoot "bin/go$exe"
    if ((Get-GoVersionOf $candidate) -ne $GoVersion) { Fail "$candidate is not $GoVersion" }
    $goExe = $candidate
  }
  if (-not $goExe) {
    $onPath = Get-Command go -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($onPath -and (Get-GoVersionOf $onPath.Path) -eq $GoVersion) { $goExe = $onPath.Path; Say "using Go from PATH: $goExe" }
  }
  if (-not $goExe) {
    if (-not (Test-IsWindowsHost)) { Fail 'outside Windows pass -GoRoot <go1.25.0 root> or use bootstrap-ipatool.sh' }
    $goArch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $goDist = "windows-$goArch"
    $goHome = Join-Path $state "$GoVersion.$goDist"
    $localGo = Join-Path $goHome 'go/bin/go.exe'
    if ((Get-GoVersionOf $localGo) -eq $GoVersion) {
      $goExe = $localGo; Say "using local Go: $goExe"
    } else {
      $dl = Join-Path $state ('dl.' + [guid]::NewGuid().ToString('N'))
      $null = New-Item -ItemType Directory -Path $dl
      try {
        $url = "https://go.dev/dl/$GoVersion.$goDist.zip"
        $zip = Join-Path $dl 'go.zip'
        Say "downloading $url"
        Invoke-Download $url $zip
        $actual = Get-Sha256 $zip
        if ($actual -ne $GoZipSha256[$goDist]) { Fail "Go archive SHA-256 mismatch: expected $($GoZipSha256[$goDist]), got $actual" }
        Say "Go archive SHA-256 OK ($actual)"
        $x = Join-Path $dl 'x'
        Expand-Zip $zip $x
        if (Test-Path -LiteralPath $goHome) { Remove-Item -LiteralPath $goHome -Recurse -Force }
        Move-Item -LiteralPath $x -Destination $goHome
      } finally { if (Test-Path -LiteralPath $dl) { Remove-Item -LiteralPath $dl -Recurse -Force } }
      if ((Get-GoVersionOf $localGo) -ne $GoVersion) { Fail "downloaded Go does not report $GoVersion" }
      $goExe = $localGo
      Say "installed $GoVersion into $goHome"
    }
  }

  # --- idempotency --------------------------------------------------------
  $binDir = Join-Path $root 'bin'
  $target = Join-Path $binDir 'ipatool.exe'
  $stamp = Join-Path $state "stamp-$Asset"
  $inputParts = @("$GoVersion $Asset", $pins.Commit, (Get-Sha256 $buildScript))
  foreach ($p in $pins.Patches) { $inputParts += (Get-Sha256 (Join-Path $root "packaging/patches/$($p.Name)")) }
  $inputs = $inputParts -join ' '

  $upToDate = $false
  if (-not $Force -and (Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $stamp)) {
    $s = [System.IO.File]::ReadAllText($stamp).Split("`n")
    if ($s.Length -ge 2 -and $s[0] -eq $inputs -and $s[1] -eq (Get-Sha256 $target) -and (Test-AllMarkers $target)) { $upToDate = $true }
  }

  if ($upToDate) {
    Say 'bin\ipatool.exe is already built from the same inputs (use -Force to rebuild)'
  } else {
    $work = Join-Path $state ('work.' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $work
    $savedEnv = @{}
    foreach ($n in 'GOOS', 'GOARCH', 'CGO_ENABLED', 'GOTOOLCHAIN', 'GOPATH', 'GOMODCACHE', 'GOCACHE', 'GOFLAGS', 'GOROOT', 'CGO_CFLAGS', 'CGO_LDFLAGS') {
      $savedEnv[$n] = [Environment]::GetEnvironmentVariable($n)
    }
    try {
      # Source at the pinned commit, checked by a hash over the whole tree.
      $srcZip = Join-Path $work 'src.zip'
      $srcUrl = "https://codeload.github.com/majd/ipatool/zip/$($pins.Commit)"
      Say "downloading ipatool source $($pins.Commit)"
      Invoke-Download $srcUrl $srcZip
      Expand-Zip $srcZip (Join-Path $work 'x')
      $src = Join-Path $work "x/ipatool-$($pins.Commit)"
      if (-not (Test-Path -LiteralPath $src -PathType Container)) { Fail 'unexpected source archive layout' }
      $treeSha = Get-TreeSha256 $src
      if ($treeSha -ne $SourceTreeSha256[$pins.Commit]) { Fail "source tree SHA-256 mismatch: expected $($SourceTreeSha256[$pins.Commit]), got $treeSha" }
      Say "source tree SHA-256 OK ($treeSha)"

      foreach ($p in $pins.Patches) {
        $pf = Join-Path $root "packaging/patches/$($p.Name)"
        $actual = Get-Sha256 $pf
        if ($actual -ne $p.Sha256) { Fail "ipatool patch $($p.Name) SHA-256 mismatch: expected $($p.Sha256), got $actual" }
        Invoke-ApplyPatch $src $pf
        Say "applied $($p.Name)"
      }

      $env:GOTOOLCHAIN = $GoVersion
      $env:GOPATH = Join-Path $state 'gopath'
      $env:GOMODCACHE = Join-Path $state 'gopath/pkg/mod'
      $env:GOCACHE = Join-Path $state 'gocache'
      $env:GOFLAGS = $null; $env:GOROOT = $null; $env:CGO_CFLAGS = $null; $env:CGO_LDFLAGS = $null
      $env:GOOS = 'windows'; $env:GOARCH = 'amd64'; $env:CGO_ENABLED = '0'

      Push-Location $src
      try {
        $reported = (Invoke-NativeText $goExe @('env', 'GOVERSION')).Trim()
        if ($reported -ne $GoVersion) { Fail "go toolchain is $reported, expected $GoVersion" }
        Say "go toolchain: $reported"
        $built = Join-Path $work "ipatool-$($pins.Version)-$Asset.exe"
        $savedPref = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & $goExe build -trimpath -buildvcs=false "-ldflags=-X github.com/majd/ipatool/v2/cmd.version=$($pins.Version)" -o $built .
        $buildExit = $LASTEXITCODE; $ErrorActionPreference = $savedPref
        if ($buildExit -ne 0) { Fail "go build failed ($buildExit)" }
      } finally { Pop-Location }

      foreach ($k in $PatchMarkers.Keys) {
        if (-not (Test-BinaryMarker $built $PatchMarkers[$k])) { Fail "built ipatool is missing the $k marker: $($PatchMarkers[$k])" }
      }

      $null = New-Item -ItemType Directory -Force -Path $binDir
      Move-Item -LiteralPath $built -Destination "$target.tmp" -Force
      Move-Item -LiteralPath "$target.tmp" -Destination $target -Force
      # ipatool is MIT licensed; keep its license next to the binary (like fetch_ipatool.py).
      Copy-Item -LiteralPath (Join-Path $src 'LICENSE') -Destination (Join-Path $binDir 'ipatool-LICENSE.txt') -Force
      [System.IO.File]::WriteAllText($stamp, ($inputs + "`n" + (Get-Sha256 $target) + "`n"))
    } finally {
      foreach ($n in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($n, $savedEnv[$n]) }
      if (Test-Path -LiteralPath $work) {
        # Go module cache files are read-only only under GOMODCACHE (outside $work).
        Remove-Item -LiteralPath $work -Recurse -Force
      }
    }
  }

  # --- report -------------------------------------------------------------
  foreach ($k in $PatchMarkers.Keys) {
    if (-not (Test-BinaryMarker $target $PatchMarkers[$k])) { Fail "bin\ipatool.exe lacks the $k marker: $($PatchMarkers[$k])" }
    Write-Host "marker ${k}: OK ($($PatchMarkers[$k]))"
  }
  if (Test-IsWindowsHost) {
    $v = (Invoke-NativeText $target @('--version')).Trim()
    if ($v -notmatch '2\.6\.0') { Fail "ipatool --version does not report 2.6.0: $v" }
    $help = Invoke-NativeText $target @('--help')
    if ($help -notmatch '--keychain-passphrase-stdin') { Fail 'ipatool --help lacks --keychain-passphrase-stdin' }
    Write-Host "ipatool --version: $v"
  }
  Write-Host "sha256 $(Get-Sha256 $target)  $target"
  Write-Host "patched ipatool ready: $target"
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-Main }
