param(
    [string]$Profile = (Split-Path -Parent $PSScriptRoot),
    [switch]$SectorErase
)
$ErrorActionPreference = 'Stop'

$image = Join-Path $Profile 'firmware\firmware.bin'
$target = Join-Path $Profile 'tools\pyocd\gd32f103c8t6.py'

Write-Output 'Flashing the Waveshare USB-TO-LoRa-HF over SWD.'
Write-Output ''
Write-Output 'Wiring, from the pad block beside the USB connector, which is'
Write-Output 'labelled 3V3 / GND / SWDIO / SWCLK on the silkscreen:'
Write-Output ''
Write-Output '   probe 3V3   -> board 3V3'
Write-Output '   probe GND   -> board GND'
Write-Output '   probe SWDIO -> board SWDIO    (PA13)'
Write-Output '   probe SWCLK -> board SWCLK    (PA14)'
Write-Output '   probe NRST  -> board NRST     (optional)'
Write-Output ''
Write-Output 'Any SWD probe works here: a CMSIS-DAP/DAPLink, or an ST-Link V2.'
Write-Output ''

if (-not (Test-Path -LiteralPath $image)) {
    throw "firmware image missing at $image. Run tools\build.ps1 first."
}

if (-not (Test-Path -LiteralPath $target)) {
      throw "pyOCD target missing at $target. This file ships with the repository; re-clone it, or check you are running from the project root."
  }

  # pyOCD drives CMSIS-DAP and ST-Link alike, and the custom target declares the
  # correct 64 KiB / 1 KiB page geometry for this part.
  if (-not (Get-Command pyocd -ErrorAction SilentlyContinue)) {
      if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
          throw 'python not found, so pyocd cannot be installed. Install it with: winget install --id Python.Python.3.12 --scope user'
      }

    Write-Output 'Installing pyocd...'
    & python -m pip install --quiet pyocd
}

Write-Output "Image : $image ($((Get-Item -LiteralPath $image).Length) bytes)"
Write-Output "Target: $target"
Write-Output ''

$pyocdArgs = @('flash', '--target', $target)

if ($SectorErase) {
    # pyOCD defaults to sector erase. Sector-only erase is usually the wrong
    # choice here, because the vendor firmware leaves the chip read-protected
    # and the flash algorithm needs a mass erase to clear that. Use it only
    # when deliberately keeping existing contents.
    Write-Output 'using sector erase, so existing flash contents are kept'
    $pyocdArgs += '--erase'
    $pyocdArgs += 'sector'
} else {
    $pyocdArgs += '--erase'
    $pyocdArgs += 'chip'
}

$pyocdArgs += $image

# Check for a probe before starting, because pyocd does not time out. With
# nothing attached it prints "Waiting for a debug probe to be connected..." and
# blocks indefinitely, so an unplugged or unseated probe looks like a frozen
# script rather than a missing piece of hardware. `pyocd list` enumerates and
# returns in a few seconds, which makes the difference between one sentence of
# advice and an apparently hung terminal.
$probeReport = (& pyocd list 2>&1 | Out-String)

if ($probeReport -notmatch 'DAP|STLink|ST-LINK|CMSIS') {
    $seen = $probeReport.Trim()
    if (-not $seen) { $seen = '(pyocd listed nothing)' }

    throw @"
No SWD probe found, so flashing would have hung.

    pyocd reports: $seen

    Wire the probe to the four pads beside the dongle's USB connector:

        3V3    SWDIO    SWCLK    GND

    SWDIO and SWCLK are the two signal wires; on this board they sit on the
    labelled 2x3 header, next to ground. Then run this again.
"@
}

Write-Output "probe    : $($probeReport.Trim() -split "`n" | Where-Object { $_ -match 'DAP|STLink|ST-LINK|CMSIS' } | Select-Object -First 1)"

Write-Output "pyocd $($pyocdArgs -join ' ')"
Write-Output ''

& pyocd @pyocdArgs

if ($LASTEXITCODE -ne 0) {
    throw "pyocd failed with exit code $LASTEXITCODE. If it complains about read
protection, use STM32CubeProgrammer to erase the chip and download the image at
0x08000000 instead."
}

Write-Output ''
Write-Output 'Flashed. Unplug the probe, then replug the dongle over USB.'
Write-Output 'Check it with:'
Write-Output "    python $(Join-Path $Profile 'tools\kissmon.py') -p COM3 info"
