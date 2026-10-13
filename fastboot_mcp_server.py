# fastboot-mcp
# Copyright (C) 2026 libertyrights
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""fastboot-mcp: a Model Context Protocol server for the Android fastboot protocol.

A thin, native wrapper around the official ``fastboot`` CLI (Android
platform-tools) that exposes bootloader-stage operations to LLM agents through
MCP tools: enumerate devices, read bootloader variables, flash/erase
partitions, unlock/lock, and reboot.

This complements EDL tooling in a Qualcomm baseband diagnostic toolchain: where
Sahara/Firehose talk to a device in emergency-download (9008) mode, fastboot
talks to a device already sitting in the bootloader/fastboot menu.

The server shells out to the ``fastboot`` binary with ``subprocess.run`` and
does not reimplement any wire protocol. Destructive operations are gated behind
an explicit ``confirm=True`` flag (see ``_guard``).
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

logger = logging.getLogger("fastboot-mcp")

# ---------------------------------------------------------------------------
# fastboot CLI discovery
# ---------------------------------------------------------------------------

FASTBOOT_BASENAME = "fastboot"
FASTBOOT_BASENAME_WIN = "fastboot.exe"

DEFAULT_TIMEOUT_S = 30
FLASH_TIMEOUT_S = 600

# reboot targets accepted by `fastboot reboot <target>`; "" is a plain reboot.
REBOOT_TARGETS = {"", "bootloader", "fastbootd", "recovery", "edl"}

# fastboot/flashing subcommands that mutate persistent bootloader state.
DESTRUCTIVE_TOKENS = {"unlock", "lock", "unlock_critical", "lock_critical"}


class FastbootError(Exception):
    """Error raised while resolving or invoking the fastboot CLI."""


def _candidate_paths() -> List[str]:
    """Common install locations for the ``fastboot`` executable."""
    local = os.environ.get("LOCALAPPDATA", "") or ""
    paths: List[str] = []

    # WinGet (Google platform-tools). The package dir carries a source hash,
    # e.g. ...\Packages\Google.PlatformTools_<source>_<hash>\platform-tools\.
    if local:
        winget = os.path.join(local, "Microsoft", "WinGet", "Packages")
        paths.extend(
            glob.glob(
                os.path.join(
                    winget, "Google.PlatformTools*", "platform-tools", "fastboot.exe"
                )
            )
        )

    paths.extend([
        r"C:\platform-tools\fastboot.exe",
        r"C:\adb\fastboot.exe",
        os.path.join(local, "Android", "Sdk", "platform-tools", "fastboot.exe"),
        r"C:\Program Files\Android\platform-tools\fastboot.exe",
        r"C:\Program Files (x86)\Android\platform-tools\fastboot.exe",
        "/usr/bin/fastboot",
        "/usr/local/bin/fastboot",
        "/opt/homebrew/bin/fastboot",
        "/opt/android-sdk/platform-tools/fastboot",
    ])
    return paths


def _find_fastboot() -> str:
    """Resolve the fastboot executable.

    Order: ``shutil.which("fastboot")`` -> known install paths -> error.
    """
    found = shutil.which(FASTBOOT_BASENAME)
    if found:
        return found
    for path in _candidate_paths():
        if path and os.path.isfile(path):
            return path
    raise FastbootError(
        "fastboot executable not found. Install Android platform-tools and add "
        "it to PATH (https://developer.android.com/tools/releases/platform-tools), "
        "or pass an explicit path via the FASTBOOT environment variable. "
        "On Windows, `winget install Google.PlatformTools` installs it under "
        "%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages."
    )


def run_fastboot(
    args: List[str], serial: Optional[str] = None, timeout_s: int = DEFAULT_TIMEOUT_S
) -> Dict[str, object]:
    """Run ``fastboot [<serial>] <args...>`` and capture its output.

    Returns ``{"returncode", "stdout", "stderr"}``. Raises :class:`FastbootError`
    if the binary cannot be found or the command times out.
    """
    exe = os.environ.get("FASTBOOT") or _find_fastboot()
    cmd = [exe]
    if serial:
        cmd += ["-s", serial]
    cmd += [str(a) for a in args]

    logger.debug("running: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise FastbootError(f"failed to execute {exe!r}: {exc}")
    except subprocess.TimeoutExpired:
        raise FastbootError(f"fastboot timed out after {timeout_s}s: {' '.join(cmd)}")

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


# ---------------------------------------------------------------------------
# Safety guard
# ---------------------------------------------------------------------------


def _guard(operation: str, command: List[str], confirm: bool) -> Optional[dict]:
    """Uniform confirm/dry-run guard for destructive fastboot operations.

    Returns ``None`` when the caller may proceed (``confirm=True``). Otherwise
    returns a refusal dict and the caller must NOT run anything. Every
    destructive tool (flash, erase, oem unlock/lock, flashing unlock/lock)
    funnels through this single helper so behaviour is identical everywhere:
    with ``confirm=False`` nothing is executed and the response states exactly
    what *would* have run.
    """
    if confirm:
        return None
    return {
        "executed": False,
        "confirm_required": True,
        "would_run": "fastboot " + " ".join(command),
        "message": (
            f"Refusing to {operation}: this is a destructive operation. "
            "Nothing was run. Re-call with confirm=True to proceed."
        ),
    }


# ---------------------------------------------------------------------------
# Tool implementations (registered on FastMCP in main())
# ---------------------------------------------------------------------------


def fastboot_devices() -> dict:
    """List devices currently in fastboot mode."""
    try:
        res = run_fastboot(["devices"])
        devices = []
        for line in str(res["stdout"]).splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            devices.append({
                "serial": parts[0],
                "mode": parts[1] if len(parts) > 1 else "fastboot",
            })
        return {
            "devices": devices,
            "returncode": res["returncode"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
        }
    except FastbootError as exc:
        return {"error": str(exc)}


def fastboot_getvar(variable: str = "all", serial: Optional[str] = None) -> dict:
    """Read a bootloader variable (default ``all``) as a ``key: value`` dict."""
    try:
        res = run_fastboot(["getvar", variable], serial=serial)
        raw = str(res["stdout"]) + "\n" + str(res["stderr"])
        values: Dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if not key or key.lower().startswith("finished"):
                continue
            values[key] = val
        return {
            "variable": variable,
            "values": values,
            "returncode": res["returncode"],
            "stderr": res["stderr"],
        }
    except FastbootError as exc:
        return {"error": str(exc)}


def fastboot_flash(
    partition: str,
    file_path: str,
    serial: Optional[str] = None,
    confirm: bool = False,
) -> dict:
    """Flash an image to a partition. Destructive: requires ``confirm=True``."""
    refusal = _guard(
        f"flash image {file_path!r} to partition {partition!r}",
        ["flash", partition, file_path],
        confirm,
    )
    if refusal is not None:
        return refusal
    try:
        res = run_fastboot(
            ["flash", partition, file_path], serial=serial, timeout_s=FLASH_TIMEOUT_S
        )
        res.update({
            "operation": "flash",
            "partition": partition,
            "file": file_path,
            "success": res["returncode"] == 0,
        })
        return res
    except FastbootError as exc:
        return {"error": str(exc)}


def fastboot_erase(
    partition: str, serial: Optional[str] = None, confirm: bool = False
) -> dict:
    """Erase a partition. Destructive: requires ``confirm=True``."""
    refusal = _guard(
        f"erase partition {partition!r}", ["erase", partition], confirm
    )
    if refusal is not None:
        return refusal
    try:
        res = run_fastboot(["erase", partition], serial=serial)
        res.update({
            "operation": "erase",
            "partition": partition,
            "success": res["returncode"] == 0,
        })
        return res
    except FastbootError as exc:
        return {"error": str(exc)}


def fastboot_reboot(target: str = "", serial: Optional[str] = None) -> dict:
    """Reboot the device. ``target`` is one of "", bootloader, fastbootd,
    recovery, edl."""
    target = (target or "").strip()
    if target not in REBOOT_TARGETS:
        return {
            "error": (
                f"invalid reboot target {target!r}; expected one of "
                f"{sorted(REBOOT_TARGETS)}"
            )
        }
    try:
        args = ["reboot"] + ([target] if target else [])
        res = run_fastboot(args, serial=serial)
        res.update({"operation": "reboot", "target": target or "system",
                    "success": res["returncode"] == 0})
        return res
    except FastbootError as exc:
        return {"error": str(exc)}


def _as_tokens(args: object) -> List[str]:
    if isinstance(args, str):
        return args.split()
    if isinstance(args, (list, tuple)):
        return [str(a) for a in args]
    return [str(args)]


def fastboot_oem(
    args: str, serial: Optional[str] = None, confirm: bool = False
) -> dict:
    """Run ``fastboot oem <args>`` (e.g. ``device-info``). Destructive oem
    subcommands (unlock/lock) require ``confirm=True``."""
    tokens = _as_tokens(args)
    if not tokens:
        return {"error": "oem requires at least one argument"}
    if any(t.lower() in DESTRUCTIVE_TOKENS for t in tokens):
        refusal = _guard(
            "run destructive oem command " + " ".join(tokens),
            ["oem"] + tokens,
            confirm,
        )
        if refusal is not None:
            return refusal
    try:
        res = run_fastboot(["oem"] + tokens, serial=serial)
        res.update({"operation": "oem", "args": tokens,
                    "success": res["returncode"] == 0})
        return res
    except FastbootError as exc:
        return {"error": str(exc)}


def fastboot_flashing(
    command: str, serial: Optional[str] = None, confirm: bool = False
) -> dict:
    """Run ``fastboot flashing <command>`` (e.g. ``get_unlock_ability``).
    Destructive subcommands (unlock/lock) require ``confirm=True``."""
    tokens = _as_tokens(command)
    if not tokens:
        return {"error": "flashing requires a subcommand"}
    if any(t.lower() in DESTRUCTIVE_TOKENS for t in tokens):
        refusal = _guard(
            "run destructive flashing command " + " ".join(tokens),
            ["flashing"] + tokens,
            confirm,
        )
        if refusal is not None:
            return refusal
    try:
        res = run_fastboot(["flashing"] + tokens, serial=serial)
        res.update({"operation": "flashing", "args": tokens,
                    "success": res["returncode"] == 0})
        return res
    except FastbootError as exc:
        return {"error": str(exc)}


TOOLS = [
    fastboot_devices,
    fastboot_getvar,
    fastboot_flash,
    fastboot_erase,
    fastboot_reboot,
    fastboot_oem,
    fastboot_flashing,
]


# ---------------------------------------------------------------------------
# MCP entrypoint
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if FastMCP is None:
        sys.stderr.write("mcp SDK not installed. Run `pip install -e .`.\n")
        sys.exit(1)

    mcp = FastMCP("fastboot-mcp")

    for tool in TOOLS:
        mcp.tool()(tool)

    mcp.run()


if __name__ == "__main__":
    main()
