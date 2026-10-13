# fastboot-mcp

> ## ⚠️ ALPHA — NOT TESTED ON REAL HARDWARE
>
> This project is an early alpha. It has **not** been exercised against a
> physical device in fastboot/bootloader mode. Treat every tool call as
> potentially destructive and verify behaviour on hardware you can afford to
> lose. You have been warned.

A **native** Python [MCP](https://modelcontextprotocol.io) server that wraps the
Android **fastboot** bootloader protocol through the official `fastboot` CLI
(Android platform-tools). It lets LLM agents perform bootloader-stage
operations: enumerate devices, read bootloader variables, flash and erase
partitions, unlock/lock, and reboot.

This is the bootloader-stage companion to EDL tooling (e.g. `sahara-mcp`) in a
Qualcomm baseband diagnostic toolchain. Where Sahara/Firehose talk to a device
in emergency-download (`9008`) mode, `fastboot-mcp` talks to a device already
sitting in its bootloader menu.

## Prerequisites

- **Android platform-tools** installed, so `fastboot` is on `PATH`.
  - Windows: `winget install Google.PlatformTools`
  - Or download from
    <https://developer.android.com/tools/releases/platform-tools>
- A device booted into **bootloader / fastboot** mode (`adb reboot bootloader`).
  - On Windows the device needs the Android bootloader interface driver.
- Python 3.8+ and the `mcp` SDK (`pip install -e .`).
- Optionally set the `FASTBOOT` environment variable to an explicit
  `fastboot.exe` path; otherwise the server searches `PATH` and common install
  locations (including the WinGet platform-tools package directory).

## Tools

All tools are prefixed `fastboot_` and return a dict (or `{"error": ...}`).

| Tool | Description |
| --- | --- |
| `fastboot_devices()` | List devices in fastboot mode as `{serial, mode}`. |
| `fastboot_getvar(variable="all", serial=None)` | Read a bootloader variable; parses `key: value` lines into a dict. |
| `fastboot_flash(partition, file_path, serial=None, confirm=False)` | Flash an image to a partition. **Destructive.** |
| `fastboot_erase(partition, serial=None, confirm=False)` | Erase a partition. **Destructive.** |
| `fastboot_reboot(target="", serial=None)` | Reboot; `target` ∈ `""`, `bootloader`, `fastbootd`, `recovery`, `edl`. |
| `fastboot_oem(args, serial=None, confirm=False)` | Run `fastboot oem <args>` (e.g. `device-info`). |
| `fastboot_flashing(command, serial=None, confirm=False)` | Run `fastboot flashing <command>` (e.g. `get_unlock_ability`). |

## Safety model

Every destructive operation is gated behind an explicit **`confirm=True`**
flag and funnels through one uniform guard helper (`_guard` in
`fastboot_mcp_server.py`):

- `fastboot_flash`, `fastboot_erase` — always require `confirm=True`.
- `fastboot_oem` — requires `confirm=True` only for destructive subcommands
  containing `unlock`/`lock`.
- `fastboot_flashing` — requires `confirm=True` only for `unlock`,
  `unlock_critical`, `lock`, `lock_critical`. Read-only subcommands such as
  `get_unlock_ability` run freely.
- `fastboot_reboot` — not gated (it does not alter persistent state).

With `confirm=False` (the default) **nothing is executed**. The tool returns:

```json
{
  "executed": false,
  "confirm_required": true,
  "would_run": "fastboot flash boot boot.img",
  "message": "Refusing to flash image 'boot.img' to partition 'boot': this is a destructive operation. Nothing was run. Re-call with confirm=True to proceed."
}
```

> **OEM unlock wipes data.** `fastboot flashing unlock` /
> `fastboot oem unlock` will typically **factory-reset the device and erase all
> user data**. Never run it on a device whose data you have not backed up.

## Install

```bash
pip install -e .
```

## Usage

Start the server (stdio transport) and point an MCP client (opencode, Claude
Desktop, …) at it:

```bash
python -m fastboot_mcp_server
```

Example: inspect a device, then flash with explicit confirmation.

```
fastboot_devices()
fastboot_getvar(variable="all")
fastboot_getvar(variable="product")
# dry run — shows what would happen, runs nothing:
fastboot_flash(partition="boot", file_path="C:\\images\\boot.img")
# for real:
fastboot_flash(partition="boot", file_path="C:\\images\\boot.img", confirm=True)
fastboot_reboot(target="bootloader")
```

## Credentials / credits

- Wraps the official Android `fastboot` CLI from **platform-tools**
  (Android Open Source Project). No AOSP code is imported, linked, or copied;
  this project only invokes the binary.
- Author: libertyrights.

## License

[GPL-3.0](LICENSE). Copyright (c) 2026 libertyrights.
