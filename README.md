# meshcore-waveshare-usb-lora

[![CI](https://github.com/neohiro/meshcore-waveshare-usb-lora/actions/workflows/ci.yml/badge.svg)](https://github.com/neohiro/meshcore-waveshare-usb-lora/actions/workflows/ci.yml)

> [!WARNING]
> **This board cannot be flashed over USB alone, and no software can change
> that.** The GD32F103's `BOOT0` pin is unconnected, the CH343's `DTR`/`RTS` lines
> go nowhere, and the ROM ISP bootloader answers at none of the baud rates tried.
> There is no USB-only flashing path, so before you start you need **one of**:
>
> - **An SWD debug probe** — CMSIS-DAP, DAPLink or ST-Link V2, about €3–5 — plus
>   four wires to the `3V3 / GND / SWDIO / SWCLK` pads. The probe is generic and
>   also flashes a dozen other boards.
> - **A board MeshCore already supports.** Heltec V3 flashes over Web Serial with
>   no probe at all; the Waveshare RP2040-LoRa-HF takes a `.uf2` drag-and-drop.
>
> Read [What you have to supply in hardware](#what-you-have-to-supply-in-hardware)
> before buying anything, and [Flashing](#flashing) for the measurements behind
> this warning.

Turns a stock Waveshare USB-TO-LoRa-HF (SKU 24515 family) into a MeshCore
**KISS modem**: the SX1262 radio runs on the dongle, and the MeshCore protocol
stack runs on your PC so a bot can use it.

Part of the [MeshCore](https://meshcore.io) ecosystem: the firmware speaks the
same KISS modem protocol as MeshCore's supported boards, so this dongle and a
stock node on your mesh talk to each other.

## What you have to supply in hardware

This is the part that catches people out, so it comes before anything else. Only
one item is unusual, and it is the one that decides whether this is a weekend
project or a shelf ornament.

| | What | Notes |
|---|---|---|
| **Required** | The Waveshare USB-TO-LoRa-HF | With its antenna screwed on |
| **Required** | **An SWD debug probe** | CMSIS-DAP, DAPLink or ST-Link V2 — **about €3–5**. This is the non-obvious dependency. |
| **Required** | Four wires | To reach the dongle's `3V3 / GND / SWDIO / SWCLK` pads. `NRST` is optional. |
| **Required** | A micro-USB cable | The one that came with the dongle |
| **Instead of the probe** | A board MeshCore already supports | [Heltec V3](https://www.heltec.org) — flashed over Web Serial, no probe at all — or the Waveshare RP2040-LoRa-HF, which takes a `.uf2` drag-and-drop. |

**Why the probe is unavoidable on this board.** It is measured, not assumed, and
the evidence is in [Flashing](#flashing): holding `KEY` while powering up does not
enter the GD32F103's ROM ISP bootloader — it enters Waveshare's own proprietary
update mode. No ROM ISP sync is answered at any of five baud rates. The USB cable
alone cannot load firmware onto this board, and no software can change that.

The probe is generic: it is not tied to this project, and the same one flashes a
dozen other boards. If you have none and do not want one, buy a supported board
instead — that path needs no tools at all, and everything else in this repository
(bot config, protocol expectations, KISS test client) applies unchanged.

## Step by step

The whole job in order, with the detail in the linked sections. If you just want
it working, `.\setup.ps1` does steps 1–5 for you.

**1. Install the software prerequisites.** Five `winget` commands and a Go
download; see [Prerequisites](#prerequisites). Nothing else is needed — the ARM
cross compiler is fetched into the repository for you.

**2. Build the firmware.**

```powershell
powershell -ExecutionPolicy Bypass -File tools\fetch-toolchain.ps1
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

That produces `firmware/firmware.bin`. Add `-XtAL` for the XTAL board variant
(`USB-To-LoRa-HF-**B**`); the default is TCXO. Details in [Build](#build).

**3. Run the tests.** Worth doing before you flash, because they are what proves
the firmware agrees with meshcore-go rather than merely compiling:

```powershell
powershell -ExecutionPolicy Bypass -File tools\ci.ps1
```

**4. Wire the probe.** Four wires, dongle unplugged:

```
probe 3V3   -> board 3V3
probe GND   -> board GND
probe SWDIO -> board SWDIO    (PA13)
probe SWCLK -> board SWCLK    (PA14)
probe NRST  -> board NRST     (optional)
```

The pads are the `2x3` header beside the USB connector, labelled
`3V3 / GND / SWDIO / SWCLK` on the silkscreen. Full detail and a photo in
[Flashing](#flashing).

**5. Flash it.**

```powershell
powershell -ExecutionPolicy Bypass -File tools\flash-swd.ps1
```

It checks for a probe first and fails in about three seconds if none is
attached, rather than waiting forever. This step erases the Waveshare firmware
permanently, so do not run it until step 4 is done.

**6. Check the modem answers.** The dongle is now a MeshCore KISS modem:

```powershell
python tools\kissmon.py -p COM3 info
```

You should get a device name, a radio configuration and packet statistics.
Nothing at all means the flash did not take; see
[Troubleshooting](#troubleshooting).

**7. Watch it receive.** Leave this running while you go and power another node,
so you can see real packets arriving:

```powershell
python tools\kissmon.py -p COM3 monitor
```

**8. Start the bot.**

```powershell
powershell -ExecutionPolicy Bypass -File run-bot.ps1 -v
```

Leave `bot/config.toml` as it is if your mesh is on the MeshCore EU/UK narrow
preset. To join a different frequency or region, see
[Configure the radio](#configure-the-radio).

## Two commands

From a fresh clone, on Windows with PowerShell:

```powershell
.\setup.ps1
```

That checks the prerequisites, fetches the ARM toolchain, builds the firmware,
runs the test gate, flashes over SWD **if a probe is attached**, checks the
modem answers, and points `bot/config.toml` at the right serial port. Whatever
it could not do — no probe wired up, say — it reports as the next thing to do
rather than failing.

Then, as the second command:

```powershell
.\run-bot.ps1
```

Options worth knowing:

| | |
|---|---|
| `.\setup.ps1 -Board xtal` | Your dongle is the XTAL variant (`-B`), not the TCXO one. Getting this wrong means driving DIO3 as a TCXO supply to a board that already has a crystal, and the symptom is a radio that never starts. |
| `.\setup.ps1 -Port COM5` | The dongle is not on the port that was detected. Only needed when several ports are attached. |
| `.\setup.ps1 -SkipTests` | Skip the test gate. Only worth it when iterating on your own code; the gate is what proves the firmware matches meshcore-go. |
| `.\setup.ps1 -DryRun` | Print the plan and change nothing. |
| `.\run-bot.ps1 -v` | Verbose logging. |

A freshly flashed dongle is already on the MeshCore EU/UK narrow preset —
869.618 MHz, SF8, 62.5 kHz, 4/8, 17 dBm — because `radio.c` applies those to the
SX1262 before it waits for any command. So `kissmon.py -p COM3 monitor` can
receive straight after flashing, without running `set-radio` first.
`tests/test_bot_config.py` holds those defaults to `bot/config.toml`: the bot
sends SetRadio when it connects, so a mismatch would not stop the bot, but it
would leave the dongle listening on the wrong frequency for anything else.

<details>
<summary>The long version, if you prefer to do it by hand</summary>

```
powershell -File tools\fetch-toolchain.ps1    # ARM cross compiler, once
powershell -File tools\build.ps1              # firmware/firmware.bin
powershell -File tools\ci.ps1                 # the full gate
powershell -File tools\flash-swd.ps1          # needs an SWD probe
python tools\kissmon.py -p COM3 info          # does the modem answer?
python tools\kissmon.py -p COM3 monitor       # watch it receive
powershell -File run-bot.ps1
```

</details>

## What this hardware actually is

The dongle is **not** one of the boards MeshCore ships firmware for. Identifying
it matters, because it rules out the easy path:

| | |
|---|---|
| MCU | GD32F103C8T6 (an STM32F103 clone) |
| Radio | SX1262 |
| USB bridge | WCH CH343 (`VID_1A86&PID_55D3`) |
| Stock firmware | Waveshare's closed-source `SX1262-LoRa-DTU`, speaks a private AT/stream protocol |

`flash flasher.meshcore.io` will not list it, and for good reason: MeshCore's
only Waveshare USB target is the **RP2040-LoRa-HF**, a completely different
board. The stock DTU firmware also cannot talk to anything but another DTU.

So this project writes its own firmware, built on
[Archie3d/waveshare-usb-lora-firmware](https://github.com/Archie3d/waveshare-usb-lora-firmware),
which replaces the DTU firmware with a real SX1262 driver, and puts a KISS TNC on
top of it. Everything except the final write to the chip is done here; see
[Flashing](#flashing) for the one hardware obstacle.

## Flashing

**You need an SWD probe, or a different board.**

**The USB cable alone is not enough on this board.** This was measured, not
assumed:

- Holding KEY while powering up lights the LEDs but does **not** enter the
  GD32F103's ROM ISP bootloader. It enters Waveshare's own *application-level*
  update mode, which answers any input with the four bytes `3F 3E 01 0A`. That
  is a proprietary handshake belonging to Waveshare's `DTU Update Tool` Windows
  binary.
- No ROM ISP bootloader answers an ISP sync byte at 115200, 230400, 57600,
  38400 or 9600 baud, with even, odd or no parity.

Reproduce that finding yourself:

```powershell
python tools\gd32_isp.py -p COM3 sweep
```

`tools\flash-swd.ps1` checks for a probe before it starts, and says so plainly if
there is none. That check is not decoration: pyOCD has no timeout, so with nothing
attached it prints `Waiting for a debug probe to be connected...` and blocks
indefinitely. Measured on a machine with no probe, the unguarded version sat there
for ten minutes; the checked version fails in about three seconds and names the
four pads.

So there are two ways forward, plus one jumper-only possibility.

### Why Waveshare's own updater cannot help

The firmware package ships `firmware-upload.exe` and four `.ws` images, so it is
worth recording why patching one of them is not a way in. Measured:

- The updater contains **no cryptography and no compression** — no AES, no hash,
  no archiver. It uses `FileStream`, `SerialPort` and a `Thread`, so it streams
  the file to the dongle verbatim and the **dongle** decrypts it.
- The `.ws` files have a flat entropy of 8.00 bits/byte across all 256 byte
  values, with no zlib, gzip, LZMA or bzip2 signature, so they are encrypted
  rather than merely compressed.
- The transform is deterministic: the `HF` and `LF` images are **byte-identical
  for their first 23,568 bytes** and differ only in the band-specific tail. That
  means no per-file nonce. No simple constant XOR works either — all 256 keys
  were tried at every offset looking for a Cortex-M vector table, with no hit.

So the key lives in the dongle's own firmware. Recovering it means reading that
firmware out of flash, which needs read protection cleared, which is the SWD job
all over again. Patching a `.ws` is a dead end.

### BOOT0 is not an option here

The ROM ISP bootloader on the GD32F103 is entered when BOOT0 is high at reset,
which would mean flashing over the existing USB cable with nothing else. The
hardware does not allow it, and the schematic settles why:

- `BOOT0` (U2 pin 44) has **no net attached**. Zoomed 3x in
  [`docs/schematic-u2-boot0-3x.png`](docs/schematic-u2-boot0-3x.png), it carries
  only the symbol's own pin stub, exactly like the unused PD0/PD1/PC13/PC14. It
  relies on the chip's internal pull-down, which is why the dongle boots its
  application. There is no pad, test point or jumper to raise it.
- The CH343's modem-control lines are **not connected either**. Zoomed 2.5x in
  [`docs/schematic-ch343-2.5x.png`](docs/schematic-ch343-2.5x.png), only `TXD`
  and `RXD` have wires; `DTR` and `RTS` are unconnected, so USB cannot assert
  reset or boot selection either.
- The GD32F103 has no USB device mode, so there is no DFU or USB bootloader.

The KEY button is **not** BOOT0 either. It is `PA5`, a GPIO the application
reads, which is why holding it reaches Waveshare's update console instead of the
ROM bootloader.

Between those, no route exists that needs nothing but the USB cable. Reaching
BOOT0 would mean soldering a wire directly to pin 44 of the LQFP48 and holding it
against 3V3 during power-up: possible, needs no purchase, but 0.5 mm pitch and
not something I would try first.

### What you do have: an SWD footprint

The same schematic shows `PA13` as `SYS_JTMS-SWDIO` and `PA14` as
`SYS_JTCK-SWCLK`, each routed to a net labelled **SWDIO** and **SWCLK** off-sheet.
On the board these land on a 2×3 pad block beside the USB connector, with the
other pads silkscreened **3V3**, **GND**, **SWDIO**, **SWCLK**.

So the board already has a Cortex Debug header, and you need four wires:

| probe | board pad | MCU pin |
|---|---|---|
| 3V3 | 3V3 | — |
| GND | GND | — |
| SWDIO | SWDIO | PA13 |
| SWCLK | SWCLK | PA14 |
| NRST | *(optional)* | pin 7, shared with the SX1262 reset |

You do not need an ST-Link specifically — any SWD probe works:

- **CMSIS-DAP / DAPLink** — the smallest and often the cheapest option.
- **ST-Link V2** — the common clone is a few euro, and if you already own one
  from another project this is free.
- Anything else pyOCD supports.

If you happen to have no probe at all but do have a spare ARM board — an
Arduino, or any other microcontroller you can bit-bang two wires with — it can
drive SWD too.

### Option A: an SWD probe (keeps your dongle, ~EUR 5)

Wire the four pads above to any SWD probe, then flash:

```powershell
powershell -ExecutionPolicy Bypass -File tools\flash-swd.ps1
```

That runs pyOCD with a target written for this part. Note pyOCD only ships
`stm32f103rc` for the family, which is 256 KiB of flash in **2 KiB pages** — the
wrong geometry for a GD32F103C8, whose 64 KiB is in **1 KiB pages**. Programming
with the wrong blocksize would corrupt the flash, so `tools/pyocd/gd32f103c8t6.py`
declares the real layout and reuses the STM32F1 flash algorithm, which the
GD32F103's flash controller is register compatible with.

The script asks pyOCD for a **chip erase**, because pyOCD otherwise defaults to
sector erase and the vendor image leaves the chip read-protected. Pass
`-SectorErase` to keep existing contents, which is only useful if the chip is
already unprotected.

If pyOCD cannot clear read protection, fall back to STM32CubeProgrammer: erase
the chip, then download `firmware/firmware.bin` at `0x08000000`. You do **not**
need Archie3d's bootloader for this — plain CubeProgrammer at `0x08000000` is
enough, since this firmware is linked for that address. Either way this erases
the vendor firmware for good, though Waveshare can supply the vendor image again.

`tools/gd32_isp.py` is not needed for any of this. It is kept because it is unit
tested against AN2606 and remains the right tool for any Waveshare LoRa dongle
variant that does expose the ROM ISP.

### Option B: buy a supported board (no rework, ~EUR 12-20)

Both of these carry the **KISS Radio Modem** role in MeshCore's flasher, which
is the same job the firmware in this repository does by hand. Verified against
the flasher's own catalogue (`flasher.meshcore.io/config.json`), which lists
`kissRadio` for both, at firmware 1.17.1:

| Board | Catalogue entry | How the firmware is delivered |
|---|---|---|
| [Heltec V3](https://www.heltec.org) | `Heltec v3`, platform `esp32` | **Web Serial** through the flasher, over the USB port |
| [Waveshare RP2040-LoRa-HF-Kit](https://www.waveshare.com/rp2040-lora.htm) | `RPI Pico 2040 + WaveShare SX1262`, platform `noflash` | download the **`.uf2`** and drag it onto the `RPI-RP2` drive |

That platform difference is the one thing worth knowing up front: the RP2040
board is *not* flashed through the browser. It is an RP2040 with a native USB
bootloader, so the role download is a UF2 file rather than a Web Serial image.

For the Heltec V3:

1. Go to <https://flasher.meshcore.io> and pick the board.
2. Choose the **KISS Radio Modem** role.
3. Flash it, then set `connection` in `bot/config.toml` to the new COM port.

Everything else in this repository applies unchanged: the bot config, the radio
settings, and the KISS test client all speak the same protocol. `kissmon info`
will work against it, so the bring-up steps below are the same.

Given that Option B removes the need for any probe or rework, it is worth
weighing against Option A before buying anything.

## Prerequisites

The hardware you need is [above](#what-you-have-to-supply-in-hardware) —
in particular the SWD probe. This section is the software side, on Windows with
PowerShell:

- The dongle, with its antenna
- This repository
- Python 3.8+ and the packages in `requirements-dev.txt`:
  `pip install -r requirements-dev.txt` — that is `pyserial` for `kissmon.py`,
  `find_port.py` and `gd32_isp.py`, plus `ruff` for the lint step and `pyocd` for
  flashing. `tools/ci.ps1` checks for them and names that file if one is missing.
- `make` — `winget install --id ezwinports.make --scope user`
- Git for Windows (libopencm3's build shells out to `printf` and `python3`)
- A host C compiler, for the native tests in `tools/test.ps1` —
  `winget install --id BrechtSanders.WinLibs.POSIX.MSVCRT --scope user`
- Go, for the contract step that runs the real bot against the real firmware —
  <https://go.dev/dl/>

The host compiler and the ARM cross compiler are different things: the native
tests build for your machine and execute here, while the firmware is built for
the GD32F103. Having only the ARM one is the usual reason `tools/test.ps1`
stops.

Everything else, including the ARM compiler, is fetched into this folder.
Flashing additionally needs the SWD probe from the hardware list above;
`tools/flash-swd.ps1` installs pyOCD itself and prints the wiring.

The last two degrade rather than fail if you skip them: without `ruff` the lint
step reports `ruff not installed, skipping`, though `tools/ci.ps1 -Strict` turns
that skip into a failure, which is how CI runs. `pyocd` is installed on demand by
`tools/flash-swd.ps1`, so you only need it if you flash.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File tools\fetch-toolchain.ps1
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

The result is `firmware/firmware.bin`.

Your dongle is the TCXO variant unless the label says `USB-TO-LoRa-HF-**B**`
(that suffix means XTAL). For the `-B` variant build with:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -XtAL
```

That difference is real, not cosmetic: the XTAL build skips powering a TCXO from
DIO3 and skips the GPIOD1 TCXO enable, because on that board a 32 MHz crystal
drives the synthesiser instead. CI builds both and fails if the two images ever
come out identical, so this path cannot rot unnoticed. The default build leaves
the TCXO image in `firmware/firmware.bin`.

## Verify the modem

After flashing, `kissmon` tells you whether the modem is alive and what it
thinks its radio settings are:

```powershell
python tools\kissmon.py -p COM3 info
```

You should see the modem answering every query, roughly:

```
> cmd 0x06  11
< pong
> cmd 0x06  11
< version 0x10 (1.0)
...
< radio  freq=869618000 Hz  bw=62500 Hz  sf=8  cr=4/8
< tx-power 17 dBm
< current-rssi -118 dBm
< noise-floor -119 dBm
< stats rx=0 tx=0 errors=0
< channel-busy no
```

`stats` counting up in `monitor` is the real proof the radio works:

```powershell
python tools\kissmon.py -p COM3 monitor
```

Each received packet is printed with the signal report that follows it:

```
11:24:03 148 bytes  snr=+6.75 dB rssi= -84 dBm  0103aabb...
```

## Configure the radio

The firmware boots on the MeshCore EU/UK narrow preset already, but set it
explicitly so you can see the modem accept it:

```powershell
python tools\kissmon.py -p COM3 setradio --freq 869618000 --bw 62500 --sf 8 --cr 8 --tx 17
```

The sync word is fixed in firmware at `0x12`, which is RadioLib's
`RADIOLIB_SX126X_SYNC_WORD_PRIVATE` — the value MeshCore's variants pass to
`radio.begin()`. Meshtastic's `0x2B` and LoRaWAN's public `0x34` would each put
you on a different network, so if you see adverts from Meshtastic nodes but no
MeshCore nodes at all, the sync word is the first thing to suspect. The firmware
also derives low data rate optimization from symbol duration exactly as RadioLib
does, so SF11 at 125 kHz and SF12 at 250 kHz work.

## Run the bot

```bash
git clone https://github.com/meshcore-go/meshcore-bot
cd meshcore-bot
go build -o meshcore-bot .
./meshcore-bot --config /path/to/meshcore-waveshare-usb-lora/bot/config.toml
```

`bot/config.toml` is set to 869.618 MHz / SF8 / BW62.5 / CR4/8, TX 17 dBm, and a
bot that answers `#testing`. `freq`, `bw`, `sf` and `cr` **must** match the rest
of your mesh.

Only one program may hold `COM3` at a time, so stop `kissmon monitor` before
starting the bot.

### Reaching the modem over TCP

A MeshCore host *dials* a TCP modem rather than listening, so the far end has to
be the one serving KISS. The bot accepts it directly:

```
connection = "tcp://192.168.1.50:8000"
```

This is exercised by the end-to-end test below, so it is known to work, but you
still need something serving KISS on that port.

## Layout

```
setup.ps1              one command: prerequisites, toolchain, build, tests, flash, modem
run-bot.ps1            the second command: run meshcore-bot with bot/config.toml
firmware/            KISS modem firmware (C, libopencm3 + FreeRTOS)
  src/kiss_frame.c     KISS framing, free of RTOS or hardware
  src/serial.c         USART transport, feeds the parser
  src/kiss.c           KISS commands and the SetHardware extension
  src/lora_params.c    bandwidth mapping and RadioLib's LDRO rule
  src/pa_config.c      high power amplifier settings by output power
  src/radio.c          SX1262 driver glue, CSMA/CAD, airtime, stats
  src/device_id.c      per-device seed from the MCU unique ID
  sx126x/              Semtech SX126x driver (vendored, unmodified)
tools/
    fetch-toolchain.ps1  downloads the ARM toolchain into toolchain/
    find_port.py        picks the dongle's serial port; refuses to guess
    kissmon.py          bring-up client: info, monitor, setradio, tx
  build.ps1            builds firmware.bin
  test.ps1             runs the firmware's protocol logic on the host
  ci.ps1               build, native tests, python tests, lint
  gd32_isp.py          USART ISP flasher (not usable on this board, see above)
  flash-swd.ps1        flashes over SWD with pyOCD, prints the wiring
  pyocd/gd32f103c8t6.py  pyOCD target with this part's real flash geometry
  kissmon.py           KISS test client
tests/
  test_kiss_frame.c    framing: parser, encoder, fuzz round trip
  test_kiss_modem.c    SetHardware dispatch, driven through the real parser
  test_lora_params.c   bandwidth mapping, RadioLib's LDRO rule, sync word
  test_pa_config.c     high power amplifier settings by output power
  kiss_server.c        exposes the firmware's protocol code over stdin/stdout
  stubs/               host stand-ins for the USART, radio and FreeRTOS mutex
  contract/            meshcore-go driven against the real firmware code
  bot_e2e_test.go    the published bot, real config, real firmware, over TCP
  test_gd32_isp.py     AN2606 frame-level tests, no hardware needed
  test_bot_config.py   bot config checked against the firmware headers
  test_pycocd_target.py  flash geometry checked against the datasheet and image
    test_vector_table.py    linked vectors checked against real handler symbols
  test_flash_script.py    flash script checked against pyOCD's real command line
  test_kissmon_contract.py  bring-up tool checked against the firmware headers
  test_ci_script.py       CI script checked for unpinned or per-run network deps
    test_repo_hygiene.py    encoding, line endings and .editorconfig agreement
    test_native_harness.py  every native suite is actually invoked by main.c
    test_tool_discovery.py  missing prerequisites fail loudly and say the fix
    test_memory_budget.py   FreeRTOS heap, task stacks and MSP headroom
    test_isr_safety.py      interrupt handlers never touch the SPI bus
    test_makefile_deps.py  header dependency tracking, so edits are not ignored
    test_find_port.py       port selection, with no hardware involved
    test_entrypoints.py    setup.ps1 / run-bot.ps1, run for real where it is safe
    mutation.py            helper for breaking source on purpose (see below)
    test_mutation.py       ...and the tests proving that helper cannot lie
    test_readme_claims.py   the counts quoted above are the counts that exist
bot/config.toml      meshcore-go/meshcore-bot configuration
```

## Tests

One command runs everything:

```powershell
powershell -ExecutionPolicy Bypass -File tools\ci.ps1
```

It builds the firmware for the GD32F103 in both board variants, runs the
firmware's protocol logic on the host, checks the modem against meshcore-go,
runs the Python tests, and lints the Python. That is about 60,347 host-side
protocol assertions, 10 contract tests, and 239 Python tests. Those three counts
are checked against reality by `tests/test_readme_claims.py`, so they cannot go
stale after a test is added.

### The RAM budget is the tightest number here

Two figures decide whether this firmware runs at all, and neither is visible to
a protocol test, because both fail on hardware rather than on the host.
`tests/test_memory_budget.py` checks them, measuring the FreeRTOS struct sizes by
compiling a probe rather than trusting remembered formulas:

| | Used | Available | Headroom |
|---|---|---|---|
| FreeRTOS heap | 5,232 B | 17,408 B (`configTOTAL_HEAP_SIZE`) | 70% free |
| Static RAM (`.data` + `.bss`) | 19,196 B | 20,480 B (SRAM) | **1,284 B of MSP** |

The heap is comfortable. The **1,284 bytes of MSP is the number to respect**.
Tasks run on the PSP, but SysTick, PendSV, SVC, the radio's `EXTI0` handler and
the USART handler all run on the MSP, which grows down from the top of RAM. An
overflow there does not crash cleanly; it corrupts memory in ways that look like
random protocol bugs.

The 17 KiB heap lives inside `.bss`, so raising `configTOTAL_HEAP_SIZE` or adding
a static buffer fails at link time, which is the good outcome. Shrinking the
footprint to buy MSP room means re-running this check rather than eyeballing it.

The **task stacks** get the same treatment. The firmware is compiled with
`-fstack-usage`, and the check reads the resulting `.su` files, so a large local
buffer is caught here rather than as memory corruption on the hardware. Only
functions that survive `--gc-sections` are considered, since a dead frame is not
a risk. The largest linked frame is `kiss_frame_received` at 104 bytes.

That check exists because the receive path had exactly this bug. The KISS
reassembly parser embeds a 256-byte buffer and used to be a local of
`serial_rx_task`, where it measured **536 bytes against a 1024-byte stack** —
over half the stack, before any callee. It is file scope now, which costs
nothing: only that task ever touches it. `serial_rx_task`'s own frame is 16 bytes.

The bound is per frame, not per call chain. A real bound needs a call graph, and
this firmware dispatches through function pointers (`handler->frame_received`, the
sx126x callbacks) that a static pass cannot resolve — so treat it as a floor on
what is safe rather than a proof.

### Guards that cannot pass quietly

A check is only worth having if you have watched it fail. Breaking something on
purpose and confirming the right test notices is the only way to tell a real
check from one that passes for the wrong reason — and it is easy to get wrong,
because the mutation can silently not apply.

That happened twice here. One compared `pathlib.Path(...) == "revert"`, which is
always False, so the "revert" branch never ran and the suite passed against an
unmodified tree. Another used a regex aimed at the wrong file, so it had no match
to make, printed `OK`, and briefly looked like a passing proof. Both are
indistinguishable from success unless you check.

So mutations go through `tests/mutation.py`, which refuses to run unless it can
show the file changed: it raises when the target is absent or ambiguous, when the
replacement is identical to the original, or when the file does not match what
was written. `preserved()` restores the original bytes afterwards, verified, even
if the body raises. `tests/test_mutation.py` covers those refusals, and includes
an end-to-end case that breaks `exti0_isr` and requires the SPI guard to notice.

### Interrupts stay off the SPI bus

`sx126x_hal.c` drives the radio with libopencm3's single-byte `spi_send` and
`spi_read` in a loop, not `spi_transfer`, which would mask interrupts for the
duration of a transfer. That is safe only because nothing in interrupt context
also uses the bus. The hardware has two handlers: `exti0_isr` clears the EXTI
flag and notifies the radio task, and `usart1_isr` moves one byte into a queue.
Both return immediately, and all radio work happens in `radio_isr_task`.

`tests/test_isr_safety.py` keeps it that way. It matters because a handler that
started a transfer mid-command would interleave two conversations on one bus and
corrupt a register write — and that presents as a radio that is *intermittently*
flaky, not as a concurrency bug, so it would be very hard to diagnose from the
symptom. Interrupt latency itself still needs hardware; only the absence of
re-entry is checked here.

Two limits that used to disagree also had to be reconciled. `KISS_MAX_FRAME_SIZE`
was 512 while every consumer rejected anything above `KISS_MAX_PAYLOAD` (255), so
a 512-byte frame was reassembled only to be thrown away. They are now the same
value, which is also what halved the parser buffer above.

Two options change what it does: `-Strict` makes a missing `meshcore-bot` fatal
rather than a warning, since its absence means the end-to-end acceptance test is
not running at all; `-XtAL` decides **which build is left in `firmware/bin`**,
which matters more than it sounds. Both variants are always built — that is the
point, since an instruction that no longer compiles is worse than none — and each
build overwrites the last, so `-XtAL` simply reverses the order.

Without `-XtAL` you are left holding the **TCXO** image. If your board is the
XTAL variant (SKU `USB-TO-LoRa-xF-B`), pass `-XtAL` or you will flash the wrong
firmware: the TCXO build drives DIO3 as a TCXO supply, and on a board that
already has a crystal that presents as a radio that never starts, which is a
thoroughly misleading symptom. The script prints which variant it left behind.

`ci.ps1` always builds with `-Clean`. That matters: the Makefile only learned to
track header dependencies partway through this project's life, and an incremental
build without them links stale objects against new ones, producing a firmware
that matches no source state. `tests/test_makefile_deps.py` keeps that fixed.

One thing this script is deliberately strict about: the contract step runs
`go test -v` and **fails if any test was skipped**, not merely if the exit code
was non-zero. Both Go suites skip themselves when their binary environment
variable is unset, and `go test` still exits `0` and prints `ok` in that case.
Run standalone, `go test ./...` will happily report success having tested
nothing - so a green CI run here means the real bot really did drive the real
firmware.

### House style

`.editorconfig` declares the formatting policy and `tests/test_repo_hygiene.py`
enforces it, so the two cannot drift apart: valid UTF-8, no BOM, LF endings, a
final newline, no trailing whitespace. These are the defects that never fail a
build but rot a repository quietly - a CRLF inside one edited function, a
truncated final newline showing up as `\ No newline at end of file` in every
later diff.

Vendored trees (`firmware/rtos/`, `firmware/sx126x/`, `firmware/libopencm3/`) are
exempt and kept byte-for-byte as upstream shipped them, so diffs against those
repositories stay clean. That exemption is itself tested: if a vendored tree is
ever normalised, the test fails and the exemption is removed deliberately rather
than quietly widening what the linter ignores.

### Contract test against meshcore-go

The real risk in this project is not the firmware compiling, it is the firmware
and the bot disagreeing on the wire in a way that leaves a modem that looks
alive but never communicates. So `tests/contract/` runs meshcore-go's actual
`hardware.KissModem` against this firmware's actual `src/kiss.c`, over a pipe:

```
meshcore-go KissModem  ──stdin/stdout pipe──>  kiss-server.exe
   (real client)                                 (real firmware protocol code,
                                                 stubbed USART and SX1262)
```

Framing is decoded with meshcore-go's own `DecodeFrame`, so both sides use their
real implementations. It needs Go on PATH; the first run downloads the pinned
meshcore-go module.

```powershell
cd tests\contract
$env:KISS_SERVER_BIN = "..\kiss-server.exe"
go test -count=1 ./...
```

This is what caught the signal-report response code: MeshCore's protocol document
lists `0x9A`, but MeshCore's own modem answers `HW_RESP(GET_SIGNAL_REPORT)`, which
is `0x99`, and that is what meshcore-go looks for. With `0x9A` the client never
enables signal reporting, so received packets arrive with no SNR or RSSI
attached. Nothing else would have revealed it.

### End-to-end acceptance test

The last seam: the real bot, reading the real `bot/config.toml`, driving the real
firmware over TCP.

```
meshcore-bot.exe --config bot/config.toml     ──tcp──>  kiss-server --tcp <port>
   published module, real TOML + transport                real firmware protocol code
```

The firmware listens because a MeshCore host dials. The test asserts the firmware
received the bot's own `SET_SIGNAL_REPORT`, `SET_RADIO` and `SET_TX_POWER`, that
it answered each one, and that the bot applied the config's radio settings and
duty cycle:

```
rx type=06 len=2  sub=19   →  tx c0 06 99 01 c0
rx type=06 len=11 sub=09   →  tx c0 06 f0 c0
rx type=06 len=2  sub=0a   →  tx c0 06 f0 c0

bot: duty_cycle_pct=1  SET_RADIO freq=869.618 bw=62.5 sf=8 cr=8  SET_TX_POWER tx=17
```

`tools/ci.ps1` installs the published `meshcore-bot` into `tools/bin` for this;
without Go the test skips.

### Firmware logic, on the host

The KISS framing and the modem's command handling are plain C with no RTOS or
hardware dependency, so they compile for the host and are executed directly:

```powershell
powershell -ExecutionPolicy Bypass -File tools\test.ps1
```

`src/kiss_frame.c` holds the framing as a byte-at-a-time parser and encoder,
and `tests/test_kiss_modem.c` drives `src/kiss.c` itself through that parser,
capturing the frames it emits. Around 60,000 assertions, including a 20,000-case
fuzz round trip over payloads dense in the delimiters. It needs a host compiler:

```powershell
winget install --id BrechtSanders.WinLibs.POSIX.MSVCRT --scope user
```

This is not decoration: the fuzz round trip caught the parser clearing the
payload length *before* reporting a frame complete, which would have made the
modem report every received command as empty and so answer none of them. The
same suite caught `GetRandom` returning the request's payload size instead of
the count the host asked for.

`tests/test_lora_params.c` pins the two values that decide whether the radio is
on the same network as everyone else: the sync word, which must be RadioLib's
`RADIOLIB_SX126X_SYNC_WORD_PRIVATE` (`0x12`), and the LDRO rule, which must
follow RadioLib's "symbol duration reaches 16 ms" test rather than any
SF-only shortcut. Both were wrong in an earlier revision of this firmware, and
both fail by silently not decoding anything.

### Python

```powershell
python -m unittest discover -s tests -v
```

Two suites. `test_gd32_isp.py` pins the exact bytes each ISP command puts on
the wire, per AN2606. That caught a miscounted bootloader echo which would have
desynced on the first page and silently corrupted an entire flash.

`test_bot_config.py` validates `bot/config.toml` against the firmware's own
headers, reading limits such as `SERIAL_BAUD`, `MESHCORE_SYNC_WORD` and the
amplifier power range out of the C source rather than restating them. Nothing
else checks that file, so a wrong bandwidth or baud rate would otherwise fail
completely silently.

### What the host tests cannot see

Every other test here runs the firmware's logic on the host, against stubs for
the USART, the radio and FreeRTOS. That is the right way to test protocol
behaviour, and it has one blind spot: the chip boots through a vector table
that no host test ever touches.

The failure modes in that blind spot are nasty, because the result is a modem
that answers KISS commands over serial and then never receives a packet. An ISR
whose name does not match the vector table entry is silently dead. A vector
pointing at the weak catch-all means that interrupt never fires at all.

So `tests/test_vector_table.py` reads the built ELF instead, and resolves each
vector to a real symbol:

- the initial stack pointer is the top of SRAM, and 8-byte aligned;
- `Reset`, `SVC`, `PendSV` and `SysTick` reach `reset_handler`,
  `sv_call_handler`, `pend_sv_handler` and `sys_tick_handler` - if the RTOS
  vectors were wrong the scheduler would simply never run;
- DIO1 reaches `exti0_isr`, following `LORA_DIO1_IRQ` in `pinout.h` through
  libopencm3's interrupt numbering to the matching vector;
- every unused IRQ shares the one catch-all handler, which is how a handler the
  linker could not resolve would first become visible;
- no handler points outside the flashed bytes.

One subtlety that cost me a wrong turn, and is easy to get wrong again: the
constant `NVIC_EXTI0_IRQ` is **not** "EXTI line 0". libopencm3 numbers the
shared interrupt lines, so EXTI0 is interrupt **6**, EXTI1 is 7 and EXTI5_10 is
23. Reading the digits out of the constant's name selects the wrong vector slot.
The test resolves the number from libopencm3's own header for that reason.

The same principle applies to memory sizes. The linker script arrived carrying
upstream's 112 KiB budget, which belongs to a larger GD32F103 than the C8 in
this dongle. Nothing failed, because a 14 KiB image fits either way - but the
linker would have accepted an image the chip could not hold, turning a build
mistake into an obscure failure at flash time. The script now declares the
part's real 64 KiB, and `tests/test_pycocd_target.py` holds the linker script
and the pyOCD target to each other.

### What is not covered

The SX1262 itself. Everything above exercises protocol and parameter logic;
nothing here proves the radio transmits or receives.

Specifically untested, and needing hardware:

- SX1262 initialisation, and whether the TCXO supply on DIO3 is correct for your
  board variant (build `-XtAL` if yours is the `-B` XTAL version).
- CSMA timing. The default is a 500 ms pre-transmit delay, 100 ms between
  channel-busy retries and p-persistence 63. Because a CAD switches the radio to
  standby, back-off makes the modem briefly deaf, so the worst case is roughly
  20 retries before it gives up. Those are the values to tune if you see heavy
  collisions; they are in `kiss_init` in `src/kiss.c` and the host can change
  them at runtime with KISS `TXDELAY`, `SLOTTIME` and `PERSISTENCE`.
- Receive path timing, including `memcpy` from interrupt-adjacent context.
- Whether the radio's `CAD` thresholds suit your channel.

Treat bring-up as unfinished until the dongle answers `kissmon info`, then watch
`kissmon monitor` for a while before trusting the bot with it.

## Troubleshooting

**`sweep` finds nothing and the vendor app still answers.** That is the normal
result on this dongle, and it is why [Flashing](#flashing) needs an ST-Link.

**No LED activity after flashing.** Serial 115200 8N1 and try `kissmon info`.
If the modem answers there but the bot does not, the bot has the port open.

**Bots see each other but not the wider mesh.** `freq`, `bw`, `sf` and `cr` must
match across the mesh. 869.525/SF11 is the old deprecated MeshCore EU preset,
not the current one, so a node left on it is invisible to a 869.618/SF8 mesh.
If you see *Meshtastic* adverts but no MeshCore nodes, the radio is on a
different sync word; see [Configure the radio](#configure-the-radio).

**Everything transmits but nothing is received.** Check `stats rx` is climbing
and compare `current-rssi` with `noise-floor`; a large gap means the antenna or
the TCXO supply is wrong.

**`RAM 92% used` in the build output.** Expected. The FreeRTOS heap is 17 KiB of
the GD32F103's 20 KiB.

## Credits

This project exists because other people published the pieces it is built from.
None of it is mine, and the thanks matter more than the list order.

### MeshCore

**[MeshCore](https://meshcore.io)** is the project this exists to join. The KISS
modem protocol implemented here is theirs — the `SetHardware` command set, the
signal-report semantics, the packet framing, the EU/UK narrow-band presets. Their
firmware for supported boards is the reference this firmware is written to agree
with, byte for byte, and the contract tests in `tests/contract/` check exactly
that using their Go implementation.

- [meshcore.io](https://meshcore.io) — the project, its firmware and its docs
- [meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore) —
  firmware, and `docs/kiss_modem_protocol.md`, which is the specification this
  firmware implements

Thanks especially to the people who documented that protocol clearly enough to
implement from, and to the [meshcore-go](https://github.com/meshcore-go)
authors, whose Go implementation is what the tests negotiate with.

### Firmware and tooling this builds on

- **[Archie3d/waveshare-usb-lora-firmware](https://github.com/Archie3d/waveshare-usb-lora-firmware)**
  — the base this firmware starts from: the SX1262 bring-up, the CSMA/CAD handling
  and the KISS layer over a Waveshare USB-TO-LoRa board.
- **[Archie3d/waveshare-usb-lora](https://github.com/Archie3d/waveshare-usb-lora)**
  — the bootloader whose layout and flashing notes this project documents, and
  whose `0x08004000` origin this build deliberately does not use.
- **[meshcore-go/meshcore-bot](https://github.com/meshcore-go/meshcore-bot)** —
  the bot that runs against this modem; pinned to a release rather than `@latest`
  so the acceptance test keeps testing what actually ships.
- **[Semtech SX126x driver](https://github.com/LoraNet/SX126x)** — the radio
  driver, vendored unmodified in `firmware/sx126x/`.
- **[RadioLib](https://github.com/jgromes/RadioLib)** — the source of the sync
  word `0x12` (`RADIOLIB_SX126X_SYNC_WORD_PRIVATE`) and of the low-data-rate
  optimisation rule, which is derived from symbol duration exactly as RadioLib
  does it rather than from spreading factor alone.

### Libraries and the hardware

- **[FreeRTOS](https://www.freertos.org)** V9.0.0 — Real Time Engineers Ltd.,
  vendored in `firmware/rtos/`.
- **[libopencm3](https://libopencm3.org)** — the bare-metal library, vendored in
  `firmware/libopencm3/`, from the
  [libopencm3-template](https://github.com/libopencm3/libopencm3-template).
  libopencm3 is LGPL-3.0, and that is the one licence here with obligations the
  MIT below does not discharge: the firmware links it, so the LGPL terms apply to
  the built `firmware.bin` and anyone redistributing it. The MIT licence covers
  this repository's own code, not the vendored trees.
- **[Waveshare](https://www.waveshare.com)** — the USB-TO-LoRa-HF board, and the
  schematics that established what is actually on it.
- **[pyOCD](https://github.com/pyocd/pyOCD)** — CMSIS-DAP and ST-Link support,
  including the custom target file for this part in `tools/pyocd/`.
