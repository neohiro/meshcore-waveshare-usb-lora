param(
    [string]$Profile = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'

$tests = Join-Path $Profile 'tests'
$stubs = Join-Path $tests 'stubs'
$src = Join-Path $Profile 'firmware\src'

# A host compiler is needed to run the firmware's protocol logic natively. Any
# gcc will do; this looks for a couple of likely places rather than requiring a
# particular install.
function Find-HostGcc {
    $candidates = @()

    $onPath = Get-Command gcc -ErrorAction SilentlyContinue
    if ($onPath) {
        $candidates += $onPath.Source
    }

    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" `
        -Filter 'gcc.exe' -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'mingw64\\bin' } |
        Select-Object -ExpandProperty FullName

    return $candidates | Select-Object -First 1
}

$gcc = Find-HostGcc

if (-not $gcc) {
    throw @'
No host C compiler found, so the native tests cannot run.

Install one with:
    winget install --id BrechtSanders.WinLibs.POSIX.MSVCRT --scope user

The ARM cross compiler used for firmware/firmware.bin is not sufficient; these
tests need to execute on the host.
'@
}

$exe = Join-Path $tests 'test-firmware.exe'
$server = Join-Path $tests 'kiss-server.exe'

$commonSources = @(
    (Join-Path $src 'kiss_frame.c'),
    (Join-Path $src 'kiss.c'),
    (Join-Path $src 'lora_params.c'),
    (Join-Path $src 'pa_config.c'),
    (Join-Path $stubs 'device_id_stub.c'),
    (Join-Path $stubs 'freertos_stub.c'),
    (Join-Path $stubs 'serial_stub.c'),
    (Join-Path $stubs 'radio_stub.c')
)

$unitSources = @(
    (Join-Path $tests 'main.c'),
    (Join-Path $tests 'test_kiss_frame.c'),
    (Join-Path $tests 'test_kiss_modem.c'),
    (Join-Path $tests 'test_lora_params.c'),
    (Join-Path $tests 'test_pa_config.c')
) + $commonSources

$baseFlags = @(
    '-std=c11', '-Wall', '-Wextra', '-Werror',
    '-O1', '-g',
    "-I$tests", "-I$stubs", "-I$src"
)

Write-Output "compiler : $gcc"
& $gcc --version | Select-Object -First 1
Write-Output ''

& $gcc @baseFlags '-o' $exe @unitSources

if ($LASTEXITCODE -ne 0) {
    throw "native test build failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $exe)) {
    throw "native test build produced no executable at $exe"
}

# The KISS server exposes the same protocol code over stdin/stdout or a TCP
# socket so the Go contract test can drive it. It is kept on disk for that
# purpose.
$serverLibs = @()

if ($env:OS -eq 'Windows_NT' -or $IsWindows) {
    # Winsock lives in its own library on Windows. It must follow the object
    # files, so it goes at the end rather than with the compile flags.
    $serverLibs += '-lws2_32'
}

& $gcc @baseFlags '-o' $server (Join-Path $tests 'kiss_server.c') @commonSources @serverLibs

if ($LASTEXITCODE -ne 0) {
    throw "kiss server build failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $server)) {
    throw "kiss server build produced no executable at $server"
}

& $exe
$code = $LASTEXITCODE

Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue

if ($code -ne 0) {
    throw "native tests FAILED (exit code $code)"
}

Write-Output ''
Write-Output 'native tests: OK'
Write-Output "kiss server: $server"
