"""Turn the desktop service on/off as a systemd user unit.

Installed from the package, the unit ships in /usr/lib/systemd/user and only needs
enabling. Run from a source checkout, a user unit pointing at the checkout's launcher
is written to ~/.config/systemd/user instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "chimp.service"
SYSTEM_UNIT = Path("/usr/lib/systemd/user") / UNIT_NAME
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
CHECKOUT = Path(__file__).resolve().parents[2]
LAUNCHER = CHECKOUT / "run-service"

DEV_UNIT = f"""[Unit]
Description=Chimp: MIDI controllers control the desktop (source checkout)
PartOf=graphical-session.target
After=graphical-session.target pipewire.service

[Service]
ExecStart={LAUNCHER}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=15, check=False)


def running_from_checkout() -> bool:
    """True when this code is a source checkout rather than the installed package."""
    return LAUNCHER.exists() and not str(Path(__file__)).startswith(sys.prefix + "/lib")


def installed() -> bool:
    return SYSTEM_UNIT.exists() or (UNIT_DIR / UNIT_NAME).exists()


def active() -> bool:
    try:
        return _systemctl("is-active", UNIT_NAME).stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def enable() -> str:
    """Enable for future logins and start now. Returns an error message or ""."""
    user_unit = UNIT_DIR / UNIT_NAME
    if running_from_checkout():
        UNIT_DIR.mkdir(parents=True, exist_ok=True)
        if not user_unit.exists() or user_unit.read_text() != DEV_UNIT:
            user_unit.write_text(DEV_UNIT)
    elif not SYSTEM_UNIT.exists():
        return f"{SYSTEM_UNIT} is missing; reinstall the package"
    for args in (("daemon-reload",), ("enable", UNIT_NAME), ("restart", UNIT_NAME)):
        r = _systemctl(*args)
        if r.returncode != 0:
            return (r.stderr or r.stdout).strip() or f"systemctl {' '.join(args)} failed"
    return ""


def disable() -> str:
    r = _systemctl("disable", "--now", UNIT_NAME)
    return "" if r.returncode == 0 else (r.stderr or r.stdout).strip()


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "enable":
        print(enable() or "enabled and started")
    elif cmd == "disable":
        print(disable() or "stopped and disabled")
    else:
        print("installed" if installed() else "not installed", "/", "running" if active() else "not running")


if __name__ == "__main__":
    main()
