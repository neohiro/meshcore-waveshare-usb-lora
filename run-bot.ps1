<#
.SYNOPSIS
    Starts the MeshCore bot against the dongle.

.DESCRIPTION
    The second half of the two-command setup. It makes sure the pinned
    meshcore-bot release is present, then runs it with bot/config.toml, which
    setup.ps1 has already pointed at the right serial port.

    Any arguments are passed straight through, so -v for verbose logging works:

        .\run-bot.ps1 -v

.EXAMPLE
    .\setup.ps1
    .\run-bot.ps1 -v
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BotArgs
)

$ErrorActionPreference = 'Stop'
$Repo = $PSScriptRoot
$Tools = Join-Path $Repo 'tools'
$Bin = Join-Path $Tools 'bin'

# Pinned deliberately: @latest would let an upstream release change, or break,
# what the acceptance test actually verified. Keep this in step with ci.ps1.
$BotVersion = 'v1.2.0'
$BotModule = "github.com/meshcore-go/meshcore-bot@$BotVersion"

$Config = Join-Path $Repo 'bot\config.toml'

if (-not (Test-Path -LiteralPath $Config)) {
    throw "missing $Config. Run .\setup.ps1 first."
}

$image = Join-Path $Repo 'firmware\firmware.bin'
if (-not (Test-Path -LiteralPath $image)) {
    throw "missing firmware\firmware.bin. Run .\setup.ps1 first."
}

# Resolve the executable name for this platform.
$exe = Join-Path $Bin 'meshcore-bot.exe'
$exeNoExt = Join-Path $Bin 'meshcore-bot'

if (-not ((Test-Path -LiteralPath $exe) -or (Test-Path -LiteralPath $exeNoExt))) {
    Write-Host "Installing $BotModule ..." -ForegroundColor Cyan

    $go = Get-Command go -ErrorAction SilentlyContinue
    if (-not $go) {
        throw 'Go is needed to build meshcore-bot. Install it from https://go.dev/dl/ then re-run.'
    }

    if (-not (Test-Path -LiteralPath $Bin)) {
        New-Item -ItemType Directory -Force -Path $Bin | Out-Null
    }

    $env:GOBIN = $Bin
    try {
        & $go.Source install $BotModule

        if ($LASTEXITCODE -ne 0) {
            throw "installing $BotModule failed (exit code $LASTEXITCODE)"
        }
    } finally {
        Remove-Item Env:\GOBIN -ErrorAction SilentlyContinue
    }
}

# Checked rather than assumed. `go install` can exit 0 having written nothing --
# a GOBIN it did not honour, a build cache that produced the binary somewhere
# else -- and running a path that is not there fails with a shell error that
# names neither the module nor the directory.
$bot = if (Test-Path -LiteralPath $exe) {
    $exe
} elseif (Test-Path -LiteralPath $exeNoExt) {
    $exeNoExt
} else {
    throw @"
meshcore-bot is not in $Bin after installing $BotModule.

`go install` reported success but left no binary there. Check that GOBIN is
honoured by your Go installation:
    go env GOBIN

Then re-run, or build it by hand into that directory:
    go build -o "$Bin\meshcore-bot.exe" $BotModule
"@
}

Write-Host "Config : $Config" -ForegroundColor DarkGray
Write-Host "Bot    : $bot ($BotVersion)" -ForegroundColor DarkGray
Write-Host 'Press Ctrl-C to stop.' -ForegroundColor DarkGray
Write-Host ''

& $bot '--config' $Config @BotArgs
exit $LASTEXITCODE
