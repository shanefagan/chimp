"""On-screen overlay of the controller you're using: what each knob and pad does, live.

Shown on Plasma's overlay layer (layer-shell), so it floats above everything, including
fullscreen games, never takes focus and lets clicks through. It appears when you touch
a control and fades out shortly after you stop.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

from . import plugins
from .config import Action

QML = Path(__file__).resolve().parents[1] / "qml" / "Overlay.qml"
HIDE_AFTER_MS = 2500
PRESS_MS = 220
DYNAMIC = {"app_volume", "game_volume", "focused_volume", "playing_volume"}
MEDIA_LABELS = {
    "playpause": "Play/Pause",
    "play": "Play",
    "pause": "Pause",
    "next": "Next",
    "previous": "Previous",
    "stop": "Stop",
}


def short_label(a: Action) -> str:
    """A few words for an action, to fit on a pad or under a knob."""
    t = a.target.strip()
    label = {
        "none": "",
        "master_volume": "Master",
        "mic_volume": "Mic",
        "app_volume": t or "App",
        "game_volume": "Game",
        "focused_volume": "Focused app",
        "playing_volume": f"Playing #{t or 1}",
        "brightness": "Brightness",
        "command": "Command",
        "mic_mute": "Mute mic",
        "output_mute": "Mute sound",
        "media": MEDIA_LABELS.get(t.lower(), "Media"),
        "launch": t.removesuffix(".desktop").split(".")[-1].split()[0] if t else "Launch",
        "shortcut": t.partition("/")[2] or t or "Shortcut",
        "obs_scene": f"Scene: {t}" if t else "OBS scene",
        "obs_record": "OBS record",
        "obs_stream": "OBS stream",
        "obs_mute": f"OBS mute {t}".strip(),
        "marker": f"Marker: {t}" if t else "Marker",
        "profile_next": "Next profile",
        "profile_prev": "Prev profile",
    }.get(a.type)
    return label if label is not None else _plugin_short(a.type)


def _plugin_short(t: str) -> str:
    pa = plugins.actions().get(t)
    return pa.short if pa else t


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


class OverlayModel(QObject):
    changed = Signal()

    def __init__(self, ctl, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self._shown = False
        self._layout = "lpd8"  # lpd8 | grid
        self._columns = 0  # grid width; 0 = automatic
        self._title = ""
        self._subtitle = ""
        self._items: list[dict] = []
        self._values: dict[object, tuple[str, int | None]] = {}  # control key -> (label, pct)
        self._pressed: dict[object, float] = {}
        self._source: tuple = ()  # what's on screen: ("lpd8", slot) or ("generic", name, prof, group)
        self._hide = QTimer(self, singleShot=True, interval=HIDE_AFTER_MS, timeout=self._on_hide)
        self._unpress = QTimer(self, singleShot=True, interval=PRESS_MS, timeout=self._refresh)
        self._engine = None

    # --- window -----------------------------------------------------------------------

    def load(self) -> bool:
        """Create the QML window (hidden). False if QML/layer-shell isn't available."""
        try:
            import PySide6.QtQuick  # noqa: F401 - registers QQuickWindow for the root object
            from PySide6.QtQml import QQmlApplicationEngine
        except ImportError:
            return False
        engine = QQmlApplicationEngine(self)
        engine.rootContext().setContextProperty("overlay", self)
        engine.load(QUrl.fromLocalFile(str(QML)))
        if not engine.rootObjects():
            return False
        self._engine = engine
        return True

    def unload(self) -> None:
        """Destroy the window before this model goes away (avoids QML errors at exit)."""
        if self._engine is not None:
            import shiboken6

            engine, self._engine = self._engine, None
            shiboken6.delete(engine)

    # --- QML properties ---------------------------------------------------------------

    @Property(bool, notify=changed)
    def shown(self) -> bool:
        return self._shown

    @Property(str, notify=changed)
    def layout(self) -> str:
        return self._layout

    @Property(int, notify=changed)
    def columns(self) -> int:
        return self._columns

    @Property(str, notify=changed)
    def title(self) -> str:
        return self._title

    @Property(str, notify=changed)
    def subtitle(self) -> str:
        return self._subtitle

    @Property("QVariantList", notify=changed)
    def items(self) -> list:
        return self._items

    @Property(str, notify=changed)
    def position(self) -> str:
        return self.ctl.cfg.overlay_position

    @Slot()
    def _on_hide(self) -> None:
        self._shown = False
        self.changed.emit()

    # --- input from the service ----------------------------------------------------------

    def enabled(self) -> bool:
        if self._engine is None or not self.ctl.cfg.overlay or self.ctl.paused:
            return False
        if not self.ctl.cfg.overlay_over_games:
            g = self.ctl.focus.game()
            f = self.ctl.focus.focused
            if g and f["fullscreen"] and f["pid"] == g["pid"]:
                return False
        return True

    def value(self, key, label: str, pct: int | None) -> None:
        """A knob/encoder change was applied (from the action runner)."""
        self._values[key] = (label, pct)
        if self._shown:
            self._refresh()

    def touch_lpd8(self, slot: int, key) -> None:
        self._touch(("lpd8", slot), key)

    def touch_generic(self, name: str, prof: int, index: int) -> None:
        dev = self.ctl.cfg.devices.get(name)
        if dev is None:
            return
        controls = dev.profiles[prof].controls
        group = controls[index].group if 0 <= index < len(controls) else ""
        self._touch(("generic", name, prof, group), (name, prof, index))

    def program_switched(self, slot: int) -> None:
        if self._shown or self.enabled():
            self._touch(("lpd8", slot), None)

    def _touch(self, source: tuple, key) -> None:
        if not self.enabled():
            return
        self._source = source
        if key is not None:
            self._pressed[key] = time.monotonic()
            self._unpress.start()
        self._shown = True
        self._hide.start()
        self._refresh()

    # --- building what's on screen ------------------------------------------------------

    def _is_pressed(self, key) -> bool:
        t = self._pressed.get(key)
        return t is not None and time.monotonic() - t < PRESS_MS / 1000

    def _value_text(self, key) -> tuple[str, float]:
        _label, pct = self._values.get(key, ("", None))
        return ("", -1.0) if pct is None else (f"{pct}%", pct / 100)

    def _knob_label(self, key, a: Action) -> str:
        """For knobs that follow an app (Game, Playing #2...), say which app they last moved."""
        short = short_label(a)
        resolved = self._values.get(key, ("", None))[0]
        if a.type in DYNAMIC and resolved and resolved.lower() != short.lower():
            return f"{short} · {resolved}" if a.type != "app_volume" else resolved
        return short

    def refresh_if_shown(self) -> None:
        if self._shown:
            self._refresh()

    def _refresh(self) -> None:
        if not self._source:
            return
        if self._source[0] == "lpd8":
            self._build_lpd8(self._source[1])
        else:
            self._build_generic(*self._source[1:])
        self.changed.emit()

    def _build_lpd8(self, slot: int) -> None:
        ctl = self.ctl
        acts = ctl.cfg.actions(slot)
        prog = ctl.programs.get(slot)
        self._layout = "lpd8"
        self._title = "LPD8 mk2"
        self._subtitle = f"Program {slot}" + ("" if acts.active() else " · MIDI only")
        items = []
        for i, a in enumerate(acts.knobs):
            key = ("lpd8", slot, i)
            text, frac = self._value_text(key)
            items.append(
                {
                    "kind": "knob",
                    "name": f"K{i + 1}",
                    "label": self._knob_label(key, a),
                    "value": text,
                    "level": frac,
                    "pressed": self._is_pressed(key),
                    "active": a.type != "none",
                }
            )
        for i, a in enumerate(acts.pads):
            key = ("pad", slot, i)
            pad = prog.pads[i] if prog else None
            on = slot == ctl.active and i < len(ctl.pad_state) and ctl.pad_state[i]
            items.append(
                {
                    "kind": "pad",
                    "name": f"PAD {i + 1}",
                    "label": short_label(a),
                    "value": "",
                    "pressed": self._is_pressed(key),
                    "on": bool(on),
                    "active": a.type != "none",
                    "offColor": _hex(pad.off_color) if pad else "#202040",
                    "onColor": _hex(pad.on_color) if pad else "#3060ff",
                }
            )
        self._items = items

    def _build_generic(self, name: str, prof: int, group: str) -> None:
        dev = self.ctl.cfg.devices.get(name)
        if dev is None:
            self._items = []
            return
        profile = dev.profiles[min(prof, len(dev.profiles) - 1)]
        from .templates import overlay_columns

        self._layout = "grid"
        self._columns = overlay_columns(name)
        self._title = name
        self._subtitle = profile.name + (f" · {group}" if group else "")
        items = []
        for i, c in enumerate(profile.controls):
            if (c.group or "") != (group or ""):
                continue
            key = (name, prof, i)
            text, frac = self._value_text(key)
            items.append(
                {
                    "kind": "pad" if c.kind == "button" else "knob",
                    "name": c.name,
                    "label": self._knob_label(key, c.action),
                    "value": text,
                    "level": frac,
                    "pressed": self._is_pressed(key),
                    "on": False,
                    "active": c.action.type != "none",
                    "offColor": "#26283a",
                    "onColor": "#3d8bff",
                }
            )
            if len(items) >= 24:
                break
        self._items = items
