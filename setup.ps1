<#
.SYNOPSIS
    Takes a fresh clone to a running MeshCore bot, in one command.

.DESCRIPTION
    Everything between "I have the code" and "the bot is talking to the mesh":
    checking the prerequisites, fetching the ARM toolchain, building the
    firmware for the right board variant, running the test gate, flashing over
    SWD, checking the modem answers, and pointing bot/config.toml at the right
    serial port.

    Hardware steps are best-effort on purpose. This script must succeed on a
    machine with no probe wired up and no dongle plugged in, because that is
    exactly the state someone is in when they first run it. A missing probe or
    an unanswered modem is reported as the next thing to do, not treated as a
    failure of the setup.

    Run the bot afterwards with .\run-bot.ps1.

.PARAMETER Board
    Which board variant to leave in firmware/firmware.bin. The TCXO board is
    the default; pass xtal for the -B variant, which has a crystal instead of a
    TCXO. Getting this wrong means driving DIO3 as a TCXO supply to a board
    that already has a crystal, and the symptom is a radio that never starts.

.PARAMETER Port
    Serial port the dongle is on. Detected automatically when there is exactly
    one candidate; pass it explicitly if you have several.

.PARAMETER SkipTests
    Skip the full test gate. Only worth it when iterating on your own code;
    the gate is what proves the firmware matches meshcore-go.

.PARAMETER NoFlash
    Do not attempt SWD flashing even if a probe is attached.

.PARAMETER DryRun
    Print the steps that would run and exit without changing anything.

.EXAMPLE
    .\setup.ps1
    Prepares everything, then flash and verify if the hardware is present.

.EXAMPLE
    .\setup.ps1 -Board xtal -Port COM5
    For the -B board variant with the dongle on COM5.
#>
[CmdletBinding()]
param(
    [ValidateSet('tcxo', 'xtal')]
    [string]$Board = 'tcxo',

    [string]$Port,

    [switch]$SkipTests,

    [switch]$NoFlash,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Repo = $PSScriptRoot
$Tools = Join-Path $Repo 'tools'

# ---------------------------------------------------------------- helpers ---

function Write-Step {
    param([string]$Name, [string]$Detail = '')
    Write-Host ''
    Write-Host "==> $Name" -ForegroundColor Cyan
    if ($Detail) {
        Write-Host "    $Detail"
    }
}

function Write-Note {
    param([string]$Message, [string]$Colour = 'Yellow')
    Write-Host "    $Message" -ForegroundColor $Colour
}

function Write-Done {
    Write-Host ''
    Write-Host 'Setup finished.' -ForegroundColor Green
}

# Reported at the end so the user always knows exactly what is left to do.
$script:Outstanding = [System.Collections.Generic.List[string]]::new()

function Add-Outstanding {
    param([string]$Message)
    $script:Outstanding.Add($Message)
}

# --------------------------------------------------------------- planning ---

$Steps = @(
    @{ Name = 'prerequisites'; Detail = 'python, a host C compiler, pyserial' },
    @{ Name = 'toolchain'; Detail = 'fetch the ARM cross compiler if it is not already here' },
    @{ Name = 'build'; Detail = "build the firmware for the $Board board variant" },
    @{ Name = 'tests'; Detail = 'run the full gate: both variants, native tests, meshcore-go contract, lint' },
    @{ Name = 'flash'; Detail = 'flash over SWD, if a probe is attached' },
    @{ Name = 'modem'; Detail = 'check the modem answers and configure the serial port' }
)

if ($SkipTests) {
    $Steps = $Steps | Where-Object { $_.Name -ne 'tests' }
}

if ($DryRun) {
    Write-Host 'Dry run: these steps would run, and nothing is changed.' -ForegroundColor Yellow
    foreach ($step in $Steps) {
        Write-Host ("  - {0,-14} {1}" -f $step.Name, $step.Detail)
    }
    Write-Host ''
    Write-Host 'Re-run without -DryRun to do it.'
    return
}

# ---------------------------------------------------------- 1. prerequisites ---

Write-Step 'prerequisites'

# Deliberately thin. tools/build.ps1 already checks python, make, a POSIX shell
# and the toolchain, and reports each with the exact install command. Duplicating
# those checks here is how this script first ended up disagreeing with it about
# where `sh` lives: Git ships it in usr\bin, which is not on PATH -- only
# Git\cmd is. What is left below is what nobody else checks.

# Python, resolved the way build.ps1 does it: Get-Command can hand back the
# Microsoft Store alias stub, which is not a real interpreter, so the candidate
# is probed before being trusted.
$pythonExe = Get-Command python -ErrorAction SilentlyContinue
if ($pythonExe) {
    & $pythonExe.Source -c 'import sys' 2>$null
    if ($LASTEXITCODE -ne 0) {
        $pythonExe = $null
    }
}

if (-not $pythonExe) {
    $pythonExe = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter 'python.exe' `
        -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch 'WindowsApps' } |
        Select-Object -First 1
}

if (-not $pythonExe) {
    throw 'python not found, or it is the Microsoft Store placeholder. Install it with: winget install --id Python.Python.3.12 --scope user'
}

if ($pythonExe -is [System.IO.FileInfo]) {
    $pythonExe = $pythonExe.FullName
}
Write-Note "python     : $pythonExe"

# A host C compiler for the native tests. Not the ARM cross compiler: that one
# cannot execute here, and having only it is the usual reason test.ps1 stops.
$gcc = Get-Command 'gcc' -ErrorAction SilentlyContinue
if (-not $gcc) {
    $gcc = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" `
        -Filter 'gcc.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
}
if (-not $gcc) {
    throw "no host C compiler found, so the native tests cannot run. Install one with: winget install --id BrechtSanders.WinLibs.POSIX.MSVCRT --scope user"
}
# Get-Command gives a CommandInfo (with .Source); the WinGet search gives a
# FileInfo (with .FullName). Reading the wrong one prints an empty path.
$gccPath = if ($gcc -is [System.IO.FileInfo]) { $gcc.FullName } else { $gcc.Source }
Write-Note "gcc        : $gccPath"

# pyserial is needed by kissmon and by the bot, and nothing else installs it.
& $pythonExe -c 'import serial' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Note 'installing pyserial, which kissmon and the bot both need'
    & $pythonExe -m pip install --quiet pyserial

    if ($LASTEXITCODE -ne 0) {
        throw "installing pyserial failed. Try it by hand: $pythonExe -m pip install pyserial"
    }
    Write-Note 'installed pyserial'
}

# Go is only needed for the contract test, which is the strongest check here, so
# its absence is reported rather than fatal.
$go = Get-Command go -ErrorAction SilentlyContinue
if (-not $go) {
    Add-Outstanding 'Go is not installed, so the meshcore-go contract test was skipped. Install it from https://go.dev/dl/ for full coverage.'
} else {
    Write-Note "go         : $($go.Source)"
}

# ------------------------------------------------------------- 2. toolchain ---

$Toolchain = Join-Path $Repo 'toolchain\arm-gcc'

Write-Step 'toolchain'
if (Test-Path (Join-Path $Toolchain 'bin\arm-none-eabi-gcc.exe')) {
    Write-Note 'ARM toolchain already present'
} else {
    Write-Note 'fetching the ARM cross compiler (about 100 MB, once)'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $Tools 'fetch-toolchain.ps1') -Dest (Join-Path $Repo 'toolchain')

    if ($LASTEXITCODE -ne 0) {
        throw "fetching the toolchain failed (exit code $LASTEXITCODE)"
    }
}

# ---------------------------------------------------------------- 3. build ---

Write-Step 'build'

Write-Note 'building'

# Arguments are built as a plain string array rather than a hashtable splat.
# A switch cannot cross the powershell.exe -File boundary as `Clean = $true`,
# and `-XtAL:($cond)` is cmdlet-only syntax that a native call forwards
# literally. Presence-only strings work for both.
$buildCmd = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', (Join-Path $Tools 'build.ps1'),
    '-Profile', $Repo,
    '-Clean'
)
if ($Board -eq 'xtal') {
    $buildCmd += '-XtAL'
}

& powershell.exe @buildCmd

if ($LASTEXITCODE -ne 0) {
    throw "the firmware build failed (exit code $LASTEXITCODE)"
}

$Image = Join-Path $Repo 'firmware\firmware.bin'
$hash = (Get-FileHash -LiteralPath $Image -Algorithm SHA256).Hash
Write-Note "firmware.bin is the $Board variant, $((Get-Item -LiteralPath $Image).Length) bytes"
Write-Note "sha256 $hash"

# ---------------------------------------------------------------- 4. tests ---

if (-not $SkipTests) {
    Write-Step 'tests'
    Write-Note 'this takes a minute; it builds both variants and runs the real bot against the firmware'

    $ciCmd = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $Tools 'ci.ps1'),
        '-Profile', $Repo
    )
    if ($Board -eq 'xtal') {
        $ciCmd += '-XtAL'
    }

    & powershell.exe @ciCmd

    if ($LASTEXITCODE -ne 0) {
        throw "the test gate failed (exit code $LASTEXITCODE). Fix that before flashing; see the log above."
    }
}

# --------------------------------------------------------------- 5. flash ---

Write-Step 'flash'

$Probes = & $pythonExe -m pyocd list 2>&1
$hasProbe = ($Probes | Select-String -Pattern 'DAP|STLink|ST-LINK|CMSIS' -Quiet)

if ($NoFlash) {
    Write-Note 'skipped, because -NoFlash was given'
} elseif (-not $hasProbe) {
    Write-Note 'no SWD probe detected, so nothing was flashed'
    Add-Outstanding @"
No SWD probe was detected, so the dongle is still running the Waveshare
    firmware. Wire the probe to the 3V3 / GND / SWDIO / SWCLK pads beside the USB
    connector, then run:  .\setup.ps1
"@
} else {
    Write-Note 'probe detected, flashing'
    $flashArgs = @{ Profile = $Repo }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $Tools 'flash-swd.ps1') @flashArgs

    if ($LASTEXITCODE -ne 0) {
        Add-Outstanding "Flashing failed (exit code $LASTEXITCODE). Run tools\flash-swd.ps1 directly to see the full pyOCD output."
    } else {
        Write-Note 'flashed' -Colour Green
    }
}

# ---------------------------------------------------------------- 6. modem ---

Write-Step 'modem'

if (-not $Port) {
    $detected = & $pythonExe (Join-Path $Tools 'find_port.py') 2>&1
    if ($LASTEXITCODE -eq 0 -and $detected) {
        # The helper prints "<port>  -- <reason>"; keep the port only.
        $Port = ($detected -split '\s+')[0]
        Write-Note "found the dongle on $Port"
    } else {
        Write-Note 'could not identify the dongle automatically'
    }
}

if (-not $Port) {
    Add-Outstanding @'
The serial port could not be identified. Plug the dongle in and run:
      python tools\find_port.py --list
    then re-run setup with -Port COMx. The bot defaults to COM3.
'@
} else {
    $Config = Join-Path $Repo 'bot\config.toml'
    $wanted = "serial://$Port"

    $current = (Select-String -LiteralPath $Config -Pattern '^connection\s*=').Line
    if ($current -notmatch [regex]::Escape($wanted)) {
        (Get-Content -LiteralPath $Config -Raw) -replace '(?m)^connection\s*=.*$', "connection = `"$wanted`"" |
            ForEach-Object { [System.IO.File]::WriteAllText($Config, $_) }
        Write-Note "bot/config.toml now uses $wanted"
    } else {
        Write-Note "bot/config.toml already uses $wanted"
    }

    Write-Note 'asking the modem to identify itself'
    $info = & $pythonExe (Join-Path $Tools 'kissmon.py') -p $Port info 2>&1
    if ($LASTEXITCODE -eq 0) {
        $info | ForEach-Object { Write-Note $_ -Colour DarkGray }
        Write-Note 'the modem answered' -Colour Green
    } else {
        Write-Note 'the modem did not answer'
        Add-Outstanding @"
The modem on $Port did not answer a KISS request. That is expected if the
    dongle still runs the Waveshare firmware, and it also happens if the flash
    did not take. Check with:  python tools\kissmon.py -p $Port monitor
"@
    }
}

# ---------------------------------------------------------------- summary ---

Write-Done

if ($Outstanding.Count -gt 0) {
    Write-Host ''
    Write-Host 'Still to do:' -ForegroundColor Yellow
    foreach ($item in $Outstanding) {
        Write-Host "  - $item" -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host 'Start the bot with:' -ForegroundColor Green
Write-Host '  .\run-bot.ps1' -ForegroundColor Green
