"""Which app is in front, which one is fullscreen, and which game is running.

- Focus: a tiny KWin script reports every window activation / fullscreen change to
  this service over D-Bus (com.performativenonsense.chimp.service). Plasma Wayland gives no other way to
  ask for the active window's process.
- Game: bpfrag's read-only system-bus interface. It knows real games by their traffic
  and Steam app id, so a fullscreen video doesn't count. Followed with signals, not
  polling: StateChanged carries the game in a menu too (GamePid stays 0 until a match).
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from PySide6.QtCore import ClassInfo, QObject, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusVariant

from . import system

BUS_NAME = "com.performativenonsense.chimp.service"
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
BPFRAG_IFACE = BPFRAG[0]


@ClassInfo({"D-Bus Interface": BUS_NAME})
class _Endpoint(QObject):
    def __init__(self, tracker: FocusTracker) -> None:
        super().__init__(tracker)
        self._tracker = tracker

    @Slot(str, str, str)
    def activeWindow(self, pid: str, fullscreen: str, app: str) -> None:  # called by the KWin script
        with contextlib.suppress(ValueError):
            self._tracker._on_window(int(pid or 0), fullscreen == "1", app)


def _plain(v):
    while isinstance(v, QDBusVariant):
        v = v.variant()
    return v


class _BpfragListener(QObject):
    """Slots for bpfrag's system-bus signals (kept off the session-bus endpoint)."""

    def __init__(self, tracker: FocusTracker) -> None:
        super().__init__(tracker)
        self._tracker = tracker

    @Slot(str, "QVariantMap")
    def stateChanged(self, state: str, info: dict) -> None:
        self._tracker._on_bpfrag_state(state, {k: _plain(v) for k, v in dict(info).items()})

    @Slot(str, str, str)
    def nameOwnerChanged(self, name: str, _old: str, new: str) -> None:
        if name != BPFRAG[0]:
            return
        if new:  # bpfrag (re)started
            self._tracker._read_bpfrag()
        else:
            self._tracker._set_bpfrag(0, "")


class FocusTracker(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.focused: dict = {"pid": 0, "name": "", "fullscreen": False}
        self.last_fullscreen: dict = {"pid": 0, "name": ""}
        self.bpfrag_game: dict = {"pid": 0, "name": ""}
        self._endpoint = _Endpoint(self)
        self._ok = False
        self._bpfrag_listener = _BpfragListener(self)

    def start(self) -> None:
        bus = QDBusConnection.sessionBus()
        if bus.registerService(BUS_NAME) and bus.registerObject(
            "/", self._endpoint, QDBusConnection.RegisterOption.ExportAllSlots
        ):
            self._ok = self._load_script()
        self._follow_bpfrag()

    def stop(self) -> None:
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

    def _follow_bpfrag(self) -> None:
        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            return
        bus.connect(
            BPFRAG[0],
            BPFRAG[1],
            BPFRAG_IFACE,
            "StateChanged",
            self._bpfrag_listener,
            "2stateChanged(QString,QVariantMap)",
        )
        bus.connect(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            self._bpfrag_listener,
            "2nameOwnerChanged(QString,QString,QString)",
        )
        self._read_bpfrag()

    def _read_bpfrag(self) -> None:
        """At start and when bpfrag (re)starts. Mid-match the properties name the game; in a
        menu they don't (only StateChanged does), so a game already in its menu is picked
        up at its next state change, or by focus meanwhile."""
        props = QDBusInterface(BPFRAG[0], BPFRAG[1], "org.freedesktop.DBus.Properties", QDBusConnection.systemBus())
        props.setTimeout(1000)

        def get(name: str):
            args = props.call("Get", BPFRAG_IFACE, name).arguments()
            return _plain(args[0]) if args else None

        state = get("State")
        if state == "match":
            with contextlib.suppress(TypeError, ValueError):
                self._set_bpfrag(int(get("GamePid") or 0), str(get("Game") or ""))
        elif state == "idle" or state is None:
            self._set_bpfrag(0, "")

    def _on_bpfrag_state(self, state: str, info: dict) -> None:
        if state == "idle":
            self._set_bpfrag(0, "")
            return
        try:
            pid = int(info.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid:  # menu or match: the game, with its pid even in a menu
            self._set_bpfrag(pid, str(info.get("game") or ""))
        else:
            self._read_bpfrag()

    def _set_bpfrag(self, pid: int, name: str) -> None:
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
