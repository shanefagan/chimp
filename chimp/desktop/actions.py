"""Runs desktop actions for any controller: buttons, absolute knobs/faders and relative encoders.

Knob movements are coalesced (only the latest position per control is applied every
30 ms) and encoder ticks are summed, so fast sweeps never queue up hundreds of commands.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from . import system
from .config import KNOB_ACTIONS, MEDIA_SHORTCUTS, PAD_ACTIONS, Action

STEP_PERCENT = 2  # per encoder tick
OSD_INTERVAL_S = 0.12


def describe(a: Action) -> str:
    table = PAD_ACTIONS if a.type in PAD_ACTIONS else KNOB_ACTIONS
    label = table.get(a.type, (a.type,))[0]
    return label + (f" ({a.target})" if a.target.strip() else "")


class ActionRunner(QObject):
    def __init__(
        self,
        obs,
        osd: Callable[[], bool],
        report: Callable[[str], None],
        audio_changed: Callable[[], None],
        targets: Callable[[], dict] = dict,
        feedback: Callable[[object, str, int | None], None] | None = None,
        level_osd: Callable[[], bool] = lambda: True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._targets = targets
        self._feedback = feedback  # (control key, label, percent) after a knob/encoder change
        self._level_osd = level_osd  # Plasma's popup for knob changes (off when the overlay shows them)
        self._key: object = None
        self.obs = obs
        self._osd = osd
        self._report = report
        self._audio_changed = audio_changed
        self._levels: dict[object, tuple[Action, float, int, str]] = {}
        self._steps: dict[object, tuple[Action, int, str]] = {}
        self._brightness: dict[str, float] = {}
        self._timer = QTimer(self, singleShot=True, interval=30, timeout=self._flush)
        # Plasma redraws its popup on every call: show at most ~8 updates a second,
        # always ending on the latest value.
        self._osd_last = 0.0
        self._osd_pending: tuple | None = None
        self._osd_timer = QTimer(self, singleShot=True, timeout=self._osd_flush)

    # --- on-screen display (throttled) -----------------------------------------------

    def _show(self, fn, *args) -> None:
        now = time.monotonic()
        wait = OSD_INTERVAL_S - (now - self._osd_last)
        if wait <= 0 and not self._osd_timer.isActive():
            self._osd_last = now
            fn(*args)
            return
        self._osd_pending = (fn, args)
        if not self._osd_timer.isActive():
            self._osd_timer.start(max(1, int(wait * 1000)))

    def _osd_flush(self) -> None:
        pending, self._osd_pending = self._osd_pending, None
        if pending:
            self._osd_last = time.monotonic()
            pending[0](*pending[1])

    def _done(self, who: str, label: str, pct: int | None, delta: int = 0) -> None:
        self._report(f"{who} → {label}: " + (f"{pct}%" if pct is not None else f"{delta:+d}"))
        if self._feedback is not None:
            self._feedback(self._key, label, pct)

    # --- buttons -------------------------------------------------------------------

    def press(self, a: Action, who: str) -> None:
        t = a.target.strip()
        if a.type == "none":
            return
        self._report(f"{who} → {describe(a)}")
        if a.type == "mic_mute":
            system.toggle_mute(system.SOURCE)
            QTimer.singleShot(120, lambda: self._after_mute("microphone"))
        elif a.type == "output_mute":
            system.toggle_mute(system.SINK)
            QTimer.singleShot(120, lambda: self._after_mute("speakers"))
        elif a.type == "media":
            system.invoke_shortcut("mediacontrol", MEDIA_SHORTCUTS.get(t.lower(), "playpausemedia"))
        elif a.type == "launch":
            system.launch(t)
        elif a.type == "shortcut":
            comp, _, name = t.partition("/")
            if not name:
                comp, name = "kwin", comp
            system.invoke_shortcut(comp, name)
        elif a.type == "command":
            system.run_command(t)
        elif a.type.startswith("obs_"):
            if not self.obs.connected:
                self._report(f"{who} → OBS isn't running")
                if self._osd():
                    self._show(system.osd_text, "dialog-warning", "OBS isn't running (or its WebSocket server is off)")
                return
            if a.type == "obs_scene":
                self.obs.request("SetCurrentProgramScene", {"sceneName": t})
            elif a.type == "obs_record":
                self.obs.request("ToggleRecord")
            elif a.type == "obs_stream":
                self.obs.request("ToggleStream")
            elif a.type == "obs_mute":
                self.obs.request("ToggleInputMute", {"inputName": t})

    def _after_mute(self, what: str) -> None:
        self._audio_changed()
        if not self._osd():
            return
        node = system.SOURCE if what == "microphone" else system.SINK
        muted = system.is_muted(node)
        if muted is None:
            return
        if what == "microphone":
            icon = "microphone-sensitivity-muted" if muted else "microphone-sensitivity-high"
        else:
            icon = "audio-volume-muted" if muted else "audio-volume-high"
        self._show(system.osd_text, icon, f"{what.capitalize()} {'muted' if muted else 'on'}")

    # --- knobs / faders (absolute) and encoders (relative) ---------------------------

    def level(self, key, a: Action, frac: float, raw: int, who: str) -> None:
        if a.type == "none":
            return
        self._levels[key] = (a, max(0.0, min(frac, 1.0)), raw, who)
        self._steps.pop(key, None)
        if not self._timer.isActive():
            self._timer.start()

    def step(self, key, a: Action, delta: int, who: str) -> None:
        if a.type == "none" or delta == 0:
            return
        prev = self._steps.get(key)
        self._steps[key] = (a, (prev[1] if prev else 0) + delta, who)
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self) -> None:
        levels, self._levels = self._levels, {}
        steps, self._steps = self._steps, {}
        for key, (a, frac, raw, who) in levels.items():
            self._key = key
            self._apply_level(a, frac, raw, who)
        for key, (a, delta, who) in steps.items():
            self._key = key
            self._apply_step(a, delta, who)

    def _apply_level(self, a: Action, frac: float, raw: int, who: str) -> None:
        pct = round(frac * 100)
        osd = self._osd() and self._level_osd()
        if a.type == "master_volume":
            system.set_volume(system.SINK, frac)
            if osd:
                self._show(system.osd_volume, pct)
        elif a.type == "mic_volume":
            system.set_volume(system.SOURCE, frac)
            if osd:
                self._show(system.osd_mic, pct)
        elif a.type in system.STREAM_ACTIONS:
            streams, label = system.resolve_streams(a.type, a.target, self._targets())
            for s in streams:
                system.set_volume(s["id"], frac)
            if osd:
                self._show(
                    system.osd_text,
                    "audio-volume-high" if pct and streams else "audio-volume-muted",
                    f"{label}: {pct}%" if streams else label,
                )
            self._done(who, label, pct)
            return
        elif a.type == "brightness":
            system.set_brightness(a.target, frac)
            self._brightness[a.target] = frac
        elif a.type == "command":
            system.run_command(a.target, value=raw, percent=pct, delta=0)
        else:
            return
        self._done(who, describe(a), pct)

    def _apply_step(self, a: Action, delta: int, who: str) -> None:
        change = delta * STEP_PERCENT / 100
        osd = self._osd() and self._level_osd()
        if a.type in ("master_volume", "mic_volume"):
            node = system.SINK if a.type == "master_volume" else system.SOURCE
            vol = system.change_volume(node, change)
            pct = round((vol or 0) * 100)
            if osd:
                self._show(system.osd_volume if a.type == "master_volume" else system.osd_mic, pct)
        elif a.type in system.STREAM_ACTIONS:
            streams, label = system.resolve_streams(a.type, a.target, self._targets())
            vol = None
            for s in streams:
                vol = system.change_volume(s["id"], change)
            pct = round((vol or 0) * 100)
            if osd:
                self._show(system.osd_text, "audio-volume-high", f"{label}: {pct}%" if streams else label)
            self._done(who, label, pct)
            return
        elif a.type == "brightness":
            current = self._brightness.get(a.target)
            if current is None:
                current = system.get_brightness(a.target) or 0.5
            new = max(0.0, min(1.0, current + delta * STEP_PERCENT / 100))
            self._brightness[a.target] = new
            system.set_brightness(a.target, new)
            pct = round(new * 100)
        elif a.type == "command":
            system.run_command(a.target, value=delta, percent=0, delta=delta)
            pct = None
        else:
            return
        self._done(who, describe(a), pct, delta)
