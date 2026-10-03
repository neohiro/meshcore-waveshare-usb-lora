param(
    [string]$Profile = (Split-Path -Parent $PSScriptRoot),
    [switch]$XtAL,
    [switch]$Clean
)
$ErrorActionPreference = 'Stop'

$fw = Join-Path $Profile 'firmware'
$tc = Join-Path $Profile 'toolchain\arm-gcc\bin'

# libopencm3 is not vendored (it is large and has its own upstream), so fetch it
# on first build.
$ocm = Join-Path $fw 'libopencm3'
if (-not (Test-Path (Join-Path $ocm 'lib\stm32\f1\gpio.c'))) {
    Write-Output 'fetching libopencm3...'
    & git clone --depth 1 https://github.com/libopencm3/libopencm3.git $ocm
    if ($LASTEXITCODE -ne 0) {
        throw "failed to clone libopencm3 (git exit code $LASTEXITCODE)"
    }
}

$make = Get-Command make -ErrorAction SilentlyContinue
if (-not $make) {
    $found = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter 'make.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $found) {
        throw "make not found. Install it with: winget install --id ezwinports.make --scope user"
    }
    $makeExe = $found.FullName
} else {
    $makeExe = $make.Source
}

if (-not (Test-Path $tc)) {
    throw "ARM toolchain missing at $tc. Run tools\fetch-toolchain.ps1 first."
}

# libopencm3's own Makefile shells out to printf, so make needs a POSIX sh on
# PATH. Git for Windows ships one; if Git is not installed this build cannot
# proceed.
$shCandidates = @(
    'C:\Program Files\Git\usr\bin\sh.exe',
    'C:\Program Files (x86)\Git\usr\bin\sh.exe'
)
$sh = $shCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $sh) {
    throw 'No sh.exe found. libopencm3 needs a POSIX shell; install Git for Windows.'
}

# libopencm3 generates its NVIC headers from irq.json with python. The real
# interpreter must come first, otherwise the Microsoft Store "python" alias
# stub shadows it and the recipe fails.
$python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($python -and (Test-Path $python.Source)) {
    $pythonDir = Split-Path -Parent $python.Source
} else {
    $python = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $pythonDir = if ($python) { Split-Path -Parent $python.FullName } else { $null }
}

if (-not $pythonDir) {
      throw @'
python not found. libopencm3 needs python3 to generate its NVIC headers.

Install it with:
    winget install --id Python.Python.3.12 --scope user
'@
  }

$env:Path = "$tc;$(Split-Path -Parent $sh);$pythonDir;$env:Path"

Write-Output "toolchain : $tc"
& (Join-Path $tc 'arm-none-eabi-gcc.exe') --version | Select-Object -First 1
Write-Output "make      : $makeExe"
Write-Output "sh        : $sh"
Write-Output "python    : $pythonDir"

# libopencm3's irq2nvic_h has a "python3" shebang, which on Windows resolves to
# the Microsoft Store alias stub rather than the real interpreter. Generate the
# NVIC headers up front with the interpreter we know works, so make finds the
# rule satisfied and never shells out to it.
$irqJson = Join-Path $ocm 'include\libopencm3\stm32\f1\irq.json'

if (Test-Path $irqJson) {
    Push-Location $ocm
    try {
        & (Join-Path $pythonDir 'python.exe') 'scripts\irq2nvic_h' 'include/libopencm3/stm32/f1/irq.json'
        if ($LASTEXITCODE -ne 0) {
            throw "irq2nvic_h failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

$makeArgs = @()

if ($XtAL) {
    $makeArgs += 'WITH_TCXO=0'
}

if ($Clean) {
    & $makeExe -C $fw clean
}

Push-Location $fw
try {
    & $makeExe @makeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$bin = Join-Path $fw 'firmware.bin'
if (-not (Test-Path $bin)) {
    throw 'firmware.bin was not produced'
}

$size = (Get-Item $bin).Length
Write-Output ''
Write-Output ("firmware.bin : {0} bytes" -f $size)
Write-Output ("sha256       : " + (Get-FileHash $bin -Algorithm SHA256).Hash.ToLower())
