param(
    [string]$Profile = (Split-Path -Parent $PSScriptRoot),
    [switch]$XtAL,
    [switch]$Strict
)
$ErrorActionPreference = 'Stop'

$results = @()

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    Write-Output ''
    Write-Output "==> $Name"
    Write-Output ('-' * 60)

    # Native tools here write to stderr as a matter of course: Python's unittest
    # prints its progress dots and its result summary there, and go test writes
    # diagnostics. If a step ever redirects or captures a native command's
    # streams, PowerShell turns that ordinary stderr into a NativeCommandError,
    # and under $ErrorActionPreference='Stop' it aborts the whole run even
    # though the command succeeded and exited 0.
    #
    # So the action runs with stderr demoted, and the outcome is decided by the
    # exit code alone. A real failure is a non-zero exit, or a throw from the
    # step itself, which stays terminating either way.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $exitCode = 0

    try {
        & $Action

        # Read the exit code immediately: a later cmdlet must not be able to
        # leave a stale value in its way, and a step that runs no native command
        # at all has not failed.
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    } finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0) {
        throw "$Name FAILED (exit code $exitCode)"
    }

    $script:results += [pscustomobject]@{ Step = $Name; Result = 'ok' }
}

# 1. The firmware itself, cross compiled for the GD32F103. Both board variants
#    are built because the README documents the XTAL one, and an instruction
#    that no longer compiles is worse than none.
Invoke-Step 'firmware build' {
    $buildScript = Join-Path $Profile 'tools\build.ps1'
    $image = Join-Path $Profile 'firmware\firmware.bin'

    # Both variants are always built; -XtAL decides which one is left on disk.
    #
    # Each build overwrites firmware.bin, so the last one wins. Building the
    # unwanted variant second means the image left behind is the one this board
    # needs -- otherwise an XTAL (-B) board is left holding a TCXO image, and
    # flashing that drives DIO3 as a TCXO supply to a crystal that is already
    # there. That failure looks like a dead radio, not a wrong build.
    $order = if ($XtAL) { @('TCXO', 'XTAL') } else { @('XTAL', 'TCXO') }
    $hashes = @{}

    foreach ($variant in $order) {
        # Written out rather than splatted: an @array on a backtick-continued
        # line is not treated as a splat, and the stray tokens reach build.ps1's
        # parameter binder as a confusing "argument name is not valid".
        if ($variant -eq 'XTAL') {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $buildScript -Profile $Profile -XtAL -Clean
        } else {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $buildScript -Profile $Profile -Clean
        }

        if ($LASTEXITCODE -ne 0) {
            throw "$variant variant build FAILED (exit code $LASTEXITCODE)"
        }

        $hashes[$variant] = (Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash
        Write-Output "$variant variant sha256: $($hashes[$variant])"
    }

    if ($hashes['XTAL'] -eq $hashes['TCXO']) {
        throw 'the XTAL and TCXO builds are identical, so WITH_TCXO is not taking effect'
    }

    $leftBehind = if ($XtAL) { 'XTAL' } else { 'TCXO' }
    Write-Output "firmware.bin is the $leftBehind variant ($($hashes[$leftBehind]))"
}

# 2. The firmware's protocol logic, compiled for the host and executed.
Invoke-Step 'native firmware tests' {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $Profile 'tools\test.ps1') -Profile $Profile
}

# 3. The modem firmware against meshcore-go, the bot that drives it.
Invoke-Step 'contract test' {
    $go = Get-Command go -ErrorAction SilentlyContinue

    if (-not $go) {
        Write-Output 'go not installed, skipping the contract test (https://go.dev/dl/)'
        return
    }

    $server = Join-Path $Profile 'tests\kiss-server.exe'

    if (-not (Test-Path -LiteralPath $server)) {
        throw "kiss server missing at $server; the native test step builds it"
    }

    $env:KISS_SERVER_BIN = $server

    # The acceptance test drives the real bot, so it needs a meshcore-bot
    # binary. The version is pinned: @latest would let an upstream release
    # change or break what these tests actually cover.
    $botVersion = 'v1.2.0'
    $botModule = "github.com/meshcore-go/meshcore-bot@$botVersion"

    $bin = Join-Path $Profile 'tools\bin'
    $botExe = Join-Path $bin 'meshcore-bot.exe'
    $botExeNoExt = Join-Path $bin 'meshcore-bot'

    # Only install when the binary is absent. Re-resolving the module on every
    # run would make each CI invocation depend on the network, which is how a
    # slow or blocked link turns into a hung or failed step.
    $haveBot = (Test-Path -LiteralPath $botExe) -or (Test-Path -LiteralPath $botExeNoExt)

    if ($haveBot) {
        Write-Output "using the existing meshcore-bot in $bin"
    } else {
        New-Item -ItemType Directory -Force -Path $bin | Out-Null
        $env:GOBIN = $bin

        Write-Output "installing $botModule"
        & $go.Source install $botModule

        if ($LASTEXITCODE -ne 0) {
            Write-Output "WARNING: could not install $botModule, so the"
            Write-Output 'end-to-end acceptance test will be skipped. That is a'
            Write-Output 'real coverage gap: the bot is not being checked against'
            Write-Output 'the firmware. Re-run with -Strict to make this fatal.'

            if ($Strict) {
                throw "installing $botModule failed with exit code $LASTEXITCODE"
            }
        }

        Remove-Item Env:\GOBIN -ErrorAction SilentlyContinue
    }

    if (Test-Path -LiteralPath $botExe) {
        $env:MESHCORE_BOT_BIN = $botExe
    } elseif (Test-Path -LiteralPath $botExeNoExt) {
        $env:MESHCORE_BOT_BIN = $botExeNoExt
    }

    Push-Location (Join-Path $Profile 'tests\contract')
    try {
        # Downloads the pinned meshcore-go module on first run.
        #
        # -count=1 matters: Go's test cache keys on the server binary's path,
        # not its contents, so without it a firmware change could be reported as
        # a stale pass.
        #
        # -v is not cosmetic. Both suites here skip themselves when their binary
        # environment variable is unset, and a skipped test still leaves `go test`
        # reporting success. Without the per-test lines below there is no way to
        # tell "everything passed" from "nothing ran".
        $goOutput = & $go.Source test -v -count=1 ./... 2>&1
        $goExit = $LASTEXITCODE

        $goOutput | ForEach-Object { Write-Output $_ }

        if ($goExit -ne 0) {
            throw "the contract tests failed (exit code $goExit)"
        }

        # A skip here means the coverage silently vanished: the acceptance test
        # is the reason this step exists, so a skipped run is not a pass.
        $skipped = $goOutput |
            Select-String -Pattern '^\s*---\s+SKIP:\s+(\S+)' |
            ForEach-Object { $_.Matches[0].Groups[1].Value }

        if ($skipped) {
            throw ("these contract tests were skipped, so they verified nothing: " +
                ($skipped -join ', ') +
                '. Run the step through tools/ci.ps1 so the binaries are built.')
        }
    } finally {
        Pop-Location
        Remove-Item Env:\KISS_SERVER_BIN -ErrorAction SilentlyContinue
        Remove-Item Env:\MESHCORE_BOT_BIN -ErrorAction SilentlyContinue
        Remove-Item Env:\GOBIN -ErrorAction SilentlyContinue
    }
}

# 4. The Python tooling.
Invoke-Step 'python tests' {
    Push-Location $Profile
    try {
        & python -m unittest discover -s tests
    } finally {
        Pop-Location
    }
}

# 5. Lint the Python.
Invoke-Step 'ruff lint' {
    $ruff = Get-Command ruff -ErrorAction SilentlyContinue

    if (-not $ruff) {
        Write-Output 'ruff not installed, skipping (pip install ruff)'
        return
    }

    & $ruff.Source check (Join-Path $Profile 'tools') (Join-Path $Profile 'tests') `
        --isolated --select E,F,W,I,UP,B,SIM --output-format concise
}

Write-Output ''
Write-Output ('=' * 60)
foreach ($r in $results) {
    Write-Output ("  {0,-24} {1}" -f $r.Step, $r.Result)
}
Write-Output ('=' * 60)
Write-Output ''
Write-Output 'ALL CHECKS PASSED'
