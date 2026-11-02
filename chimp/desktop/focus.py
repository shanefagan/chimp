"""Which app is in front, which one is fullscreen, and which game is running.

- Focus: a tiny KWin script reports every window activation / fullscreen change to
  this service over D-Bus (org.chimp.Service). Plasma Wayland gives no other way to
  ask for the active window's process.
- Game: bpfrag's read-only system-bus interface (GamePid during a match). It knows
  real games by their traffic and Steam app id, so a fullscreen video doesn't count.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from PySide6.QtCore import ClassInfo, QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusConnection

from . import system

BUS_NAME = "org.chimp.Service"
SCRIPT_NAME = "chimp-focus"
SCRIPT_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "chimp-focus.js"

KWIN_SCRIPT = f"""
function report(w) {{
    if (!w) {{
        callDBus("{BUS_NAME}", "/", "{BUS_NAME}", "activeWindow", "0", "0", "");
        return;
    }}
    callDBus("{BUS_NAME}", "/", "{BUS_NAME}", "activeWindow",
             String(w.pid), w.fullScreen ? "1" : "0", String(w.resourceClass || ""));
}}
function hook(w) {{
    if (w && w.fullScreenChanged) {{
        w.fullScreenChanged.connect(function () {{ if (workspace.activeWindow === w) report(w); }});
    }}
}}
workspace.windowList().forEach(hook);
workspace.windowAdded.connect(hook);
workspace.windowActivated.connect(report);
report(workspace.activeWindow);
"""

BPFRAG = ("com.performativenonsense.Bpfrag1", "/com/performativenonsense/Bpfrag1")


@ClassInfo({"D-Bus Interface": BUS_NAME})
class _Endpoint(QObject):
    def __init__(self, tracker: FocusTracker) -> None:
        super().__init__(tracker)
        self._tracker = tracker

    @Slot(str, str, str)
    def activeWindow(self, pid: str, fullscreen: str, app: str) -> None:  # called by the KWin script
        with contextlib.suppress(ValueError):
            self._tracker._on_window(int(pid or 0), fullscreen == "1", app)


class FocusTracker(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.focused: dict = {"pid": 0, "name": "", "fullscreen": False}
        self.last_fullscreen: dict = {"pid": 0, "name": ""}
        self.bpfrag_game: dict = {"pid": 0, "name": ""}
        self._endpoint = _Endpoint(self)
        self._ok = False
        self._bpfrag_timer = QTimer(self, interval=2000, timeout=self._poll_bpfrag)

    def start(self) -> None:
        bus = QDBusConnection.sessionBus()
        if bus.registerService(BUS_NAME) and bus.registerObject(
            "/", self._endpoint, QDBusConnection.RegisterOption.ExportAllSlots
        ):
            self._ok = self._load_script()
        self._poll_bpfrag()
        self._bpfrag_timer.start()

    def stop(self) -> None:
        self._bpfrag_timer.stop()
        system.run(
            [
                "busctl",
                "--user",
                "call",
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting",
                "unloadScript",
                "s",
                SCRIPT_NAME,
            ],
            timeout=2.0,
        )
        QDBusConnection.sessionBus().unregisterService(BUS_NAME)

    @property
    def working(self) -> bool:
        return self._ok

    def _load_script(self) -> bool:
        try:
            SCRIPT_PATH.write_text(KWIN_SCRIPT)
        except OSError:
            return False
        base = ["busctl", "--user", "call", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting"]
        for name in (SCRIPT_NAME, "knobdeck-focus", "lpd8mk2-desktop-focus"):  # ours, and earlier names'
            system.run([*base, "unloadScript", "s", name], timeout=2.0)
        out = system.run([*base, "loadScript", "ss", str(SCRIPT_PATH), SCRIPT_NAME], timeout=2.0)
        system.run([*base, "start"], timeout=2.0)
        return out.strip().startswith("i ") and not out.strip().endswith("-1")

    def _on_window(self, pid: int, fullscreen: bool, app: str) -> None:
        name = app or system.proc_name(pid)
        new = {"pid": pid, "name": name, "fullscreen": fullscreen}
        if fullscreen and pid and system.looks_like_game(pid, app):
            self.last_fullscreen = {"pid": pid, "name": name}
        if new != self.focused:
            self.focused = new
            self.changed.emit()

    def _poll_bpfrag(self) -> None:
        out = system.run(
            [
                "busctl",
                "--system",
                "--json=short",
                "get-property",
                *BPFRAG,
                "com.performativenonsense.Bpfrag1",
                "GamePid",
                "Game",
            ],
            timeout=1.0,
        )
        pid, name = 0, ""
        try:
            # get-property with two names prints one JSON object per line
            lines = [json.loads(line)["data"] for line in out.splitlines() if line.strip()]
            pid, name = int(lines[0]), str(lines[1])
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        new = {"pid": pid, "name": name}
        if new != self.bpfrag_game:
            self.bpfrag_game = new
            self.changed.emit()

    # --- what the "game" and "focused" knobs point at ----------------------------------------

    def game(self) -> dict | None:
        """{pid, name, source}: bpfrag's game in a match, else the fullscreen game in front,
        else the last fullscreen game while it's still running. "Game" = Steam/Proton/Wine/
        gamescope (see system.looks_like_game), so a fullscreen browser or video doesn't count."""
        g = self.bpfrag_game
        if g["pid"] and system.pid_alive(g["pid"]):
            return {"pid": g["pid"], "name": g["name"], "source": "bpfrag"}
        f = self.focused
        if f["fullscreen"] and f["pid"] and system.looks_like_game(f["pid"], f["name"]):
            return {"pid": f["pid"], "name": f["name"], "source": "fullscreen"}
        lf = self.last_fullscreen
        if lf["pid"] and system.pid_alive(lf["pid"]):
            return {"pid": lf["pid"], "name": lf["name"], "source": "fullscreen"}
        return None

    def focused_app(self) -> dict | None:
        f = self.focused
        return {"pid": f["pid"], "name": f["name"], "source": "focus"} if f["pid"] else None

    def snapshot(self) -> dict:
        return {"game": self.game(), "focused": self.focused_app()}
