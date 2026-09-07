# Windows PowerShell 5.1 (Windows 10/11).
[CmdletBinding()]
param(
    [switch]$WithGame,
    [switch]$PrepareOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-VerifiedDownload {
    param([string]$Url, [string]$Sha256, [string]$Destination)

    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) {
            return $Destination
        }
        Write-Host "Replacing invalid cached download: $Destination"
        Remove-Item -LiteralPath $Destination -Force
    }

    $partial = "$Destination.$([Guid]::NewGuid().ToString('N')).download"
    try {
        Write-Host "Downloading $Url"
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Sha256) {
            throw "SHA256 mismatch for $Url. The downloaded file will not be used."
        }
        Move-Item -LiteralPath $partial -Destination $Destination -Force
    }
    finally {
        if (Test-Path -LiteralPath $partial) {
            Remove-Item -LiteralPath $partial -Force
        }
    }
    return $Destination
}

function Assert-CachePath {
    param([string]$Path)

    $candidate = [IO.Path]::GetFullPath($Path)
    if (-not $candidate.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The tool cache must be inside the repository.'
    }
    while ($candidate -ne $repoRoot) {
        if (Test-Path -LiteralPath $candidate) {
            $entry = Get-Item -LiteralPath $candidate -Force
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "The tool cache must not contain a junction or symbolic link: $candidate"
            }
        }
        $candidate = [IO.Path]::GetDirectoryName($candidate)
    }
}

try {
    # Use Windows PowerShell modules even when called from PowerShell 7.
    Import-Module ([IO.Path]::Combine($PSHOME, 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')) -Force
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'The development bootstrap requires 64-bit Windows 10 or 11.'
    }
    $repoRoot = $PSScriptRoot
    $config = Get-Content -LiteralPath (Join-Path $repoRoot 'tools/bootstrap.json') -Raw | ConvertFrom-Json
    $pythonVersion = (Get-Content -LiteralPath (Join-Path $repoRoot 'tools/.python-version') -Raw).Trim()
    if ($config.python.version -ne $pythonVersion) {
        throw 'Update tools/bootstrap.json to match tools/.python-version, including the verified download hash.'
    }

    # Enable TLS 1.2 for older Windows PowerShell defaults.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $toolCache = [IO.Path]::GetFullPath((Join-Path $repoRoot '.cache/tools'))
    $downloads = Join-Path $toolCache 'downloads'
    Assert-CachePath $downloads
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    Write-Host 'Checking portable tools...'
    $pythonArchive = Get-VerifiedDownload -Url $config.python.url -Sha256 $config.python.sha256 -Destination (Join-Path $downloads "python-$pythonVersion-embed-amd64.zip")
    $sevenZip = Get-VerifiedDownload -Url $config.seven_zip.url -Sha256 $config.seven_zip.sha256 -Destination (Join-Path $downloads "7zr-$($config.seven_zip.version).exe")

    # Re-extract verified Python to discard stale or modified runtime files.
    $pythonDirectory = [IO.Path]::GetFullPath((Join-Path $toolCache "python-$pythonVersion-amd64"))
    if (-not $pythonDirectory.StartsWith($toolCache + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The Python extraction directory must be inside .cache/tools.'
    }
    if (Test-Path -LiteralPath $pythonDirectory) {
        Assert-CachePath $pythonDirectory
        $links = Get-ChildItem -LiteralPath $pythonDirectory -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }
        if ($links) { throw 'The cached Python directory contains a junction or symbolic link; remove that link and rerun.' }
        Remove-Item -LiteralPath $pythonDirectory -Recurse -Force
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($pythonArchive, $pythonDirectory)

    $env:SEVEN_ZIP = $sevenZip
    $buildArguments = @('-X', 'utf8', (Join-Path $repoRoot 'tools/build.py'), 'dev')
    if ($WithGame) { $buildArguments += '--with-game' }
    if ($PrepareOnly) { $buildArguments += '--prepare-only' }
    Push-Location -LiteralPath $repoRoot
    try {
        & (Join-Path $pythonDirectory 'python.exe') @buildArguments
        $devExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    exit $devExitCode
}
catch {
    [Console]::Error.WriteLine("Development setup failed: $($_.Exception.Message)")
    exit 1
}
