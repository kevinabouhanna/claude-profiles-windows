<#
.SYNOPSIS
    Build the Claude Profiles release artifacts: installer, portable zip, checksums.

.DESCRIPTION
    The same script runs locally and in GitHub Actions, so a release can be
    reproduced on any Windows machine with Python and Inno Setup 6.

      release\ClaudeProfiles-Setup-X.Y.Z.exe
      release\ClaudeProfiles-X.Y.Z-win-x64-portable.zip
      release\SHA256SUMS.txt

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\build_release.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

if (-not $Python) {
    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}

if (-not $Iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $Iscc) {
        $onPath = Get-Command iscc -ErrorAction SilentlyContinue
        if ($onPath) { $Iscc = $onPath.Source }
    }
    if (-not $Iscc) { throw "Inno Setup 6 not found. Install it (winget install JRSoftware.InnoSetup) or pass -Iscc." }
}

$Version = (& $Python tools\release.py version).Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw "could not read the version" }
Step "Building Claude Profiles $Version"

$Out = Join-Path $Root "release"
if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out | Out-Null

Step "Rendering the icon"
Invoke-Checked $Python @("tools\make_icon.py")

Step "Freezing with PyInstaller"
Invoke-Checked $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "claude_profiles.spec")

$AppDir = Join-Path $Root "dist\Claude Profiles"
$Exe = Join-Path $AppDir "ClaudeProfiles.exe"
$Stamped = (Get-Item $Exe).VersionInfo.ProductVersion
if ($Stamped -ne $Version) { throw "exe reports version '$Stamped', expected '$Version'" }

Step "Verifying the build"
Invoke-Checked $Python @("tools\verify_build.py", $Exe)

Step "Packing the portable zip"
$Zip = Join-Path $Out "ClaudeProfiles-$Version-win-x64-portable.zip"
Compress-Archive -Path $AppDir -DestinationPath $Zip -CompressionLevel Optimal

Step "Compiling the installer"
Invoke-Checked $Iscc @("/Q", "/DAppVersion=$Version", "installer\ClaudeProfiles.iss")

Step "Writing checksums"
$Sums = Get-ChildItem $Out -File | Sort-Object Name | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
}
# LF endings and no BOM, so `sha256sum -c SHA256SUMS.txt` works as-is.
[System.IO.File]::WriteAllText((Join-Path $Out "SHA256SUMS.txt"), ($Sums -join "`n") + "`n")

Step "Done"
Get-ChildItem $Out | Format-Table Name, @{ n = "Size (MB)"; e = { "{0:N1}" -f ($_.Length / 1MB) } } -AutoSize
