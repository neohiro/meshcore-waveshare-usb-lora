<#
.SYNOPSIS
    Downloads the ARM cross compiler into toolchain/.

.DESCRIPTION
    Only the ARM toolchain is vendored. GNU make is deliberately *not* fetched:
    tools/build.ps1 already finds make on PATH or in the winget package cache,
    and throws with the exact install command when it is missing. Fetching a
    second copy was redundant, and it was also the fragile part of this script.

    That fragility was not theoretical. A fresh clone fetched make from
    SourceForge, which served a 535 KB HTML mirror-selection page instead of the
    archive. Expand-Archive then failed with "End of Central Directory record
    could not be found", which says nothing about the cause and nothing about
    what to do. So every download is now checked before it is unpacked: a zip
    starts with "PK", and anything else is reported as what it actually is.

.EXAMPLE
    powershell -File tools\fetch-toolchain.ps1
#>
param(
    [string]$Dest = (Join-Path (Split-Path -Parent $PSScriptRoot) 'toolchain')
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# name / url / directory it unpacks into.
$items = @(
    @{
        name = 'xpack-arm-none-eabi-gcc.zip'
        url  = 'https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v15.2.1-1.1/xpack-arm-none-eabi-gcc-15.2.1-1.1-win32-x64.zip'
        dir  = 'arm-gcc'
        hint = 'about 100 MB, from GitHub releases'
    }
)

# A zip archive's local file header starts with "PK\x03\x04". Checking this turns
# a redirect page, a captive-portal login form or a proxy error page into a
# sentence that says what happened.
function Test-ZipFile {
    param([string]$Path)

    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $header = New-Object byte[] 4
        if ($stream.Read($header, 0, 4) -lt 4) {
            return $false
        }
        return ($header[0] -eq 0x50 -and $header[1] -eq 0x4B)
    } finally {
        $stream.Dispose()
    }
}

function Get-FileKind {
    param([string]$Path)

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $take = [Math]::Min(64, $bytes.Length)
    $head = [System.Text.Encoding]::ASCII.GetString($bytes, 0, $take)

    if ($head -match '^\s*<') { return 'an HTML page' }
    if ($head -match '^\s*[{]') { return 'a JSON response' }
    if ($take -eq 0) { return 'an empty file' }
    return 'something that is not a zip archive'
}

foreach ($it in $items) {
    $zip = Join-Path $Dest $it.name
    $target = Join-Path $Dest $it.dir

    if (Test-Path -LiteralPath $target) {
        Write-Output "SKIP $($it.dir) (already present)"
        continue
    }

    if (-not (Test-Path -LiteralPath $zip)) {
        Write-Output "GET  $($it.url)  [$($it.hint)]"
        Invoke-WebRequest -Uri $it.url -OutFile $zip
    }

    if (-not (Test-ZipFile -Path $zip)) {
        $kind = Get-FileKind -Path $zip
        $size = (Get-Item -LiteralPath $zip).Length
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue

        throw @"
$($it.name): the download was $kind ($size bytes), not a zip archive.
    URL: $($it.url)

    Something between you and the server answered instead -- typically a mirror
    selection page, a captive portal, a proxy, or a rate limit. The partial file
    has been deleted so the next run starts clean.

    Try again, and if it keeps happening download the archive from the URL above
    in a browser and extract it to:  $target
"@
    }

    Write-Output "UNZIP $($it.name) -> $($it.dir)"
    Expand-Archive -LiteralPath $zip -DestinationPath "$target.tmp" -Force
    $inner = Get-ChildItem -LiteralPath "$target.tmp" -Directory | Select-Object -First 1
    if ($inner) {
        Move-Item -LiteralPath $inner.FullName -Destination $target
    } else {
        Move-Item -LiteralPath "$target.tmp" -Destination $target
    }
    Remove-Item -LiteralPath "$target.tmp" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Write-Output "OK   $($it.dir)"
}

Write-Output 'DONE'
