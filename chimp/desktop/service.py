"""Background service: MIDI controllers control the desktop.

Run with `python -m chimp.desktop` (the systemd user unit does this).

- LPD8 mk2: actions per program; the service follows whichever program is active on
  the device, and drives its pad LEDs.
- Any other controller (KO II, MicroFreak, ...): learned controls grouped in profiles;
  buttons can switch profiles, and so can the tray menu.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QFileSystemWatcher, QObject, QTimer, Signal

from .. import protocol as p
from ..device import Lpd8Device
from . import audio, system
from .actions import ActionRunner
from .config import CONFIG_PATH, STATEFUL_PADS, DesktopConfig
from .focus import FocusTracker
from .learn import _address, decode
from .midi import MidiHub
from .obs import ObsClient

CLIENT_NAME = "Chimp Service"
LPD8_CLIENT = "LPD8 mk2"
STATUS_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "chimp.json"
HINT_INTERVAL_S = 20


def log(*args) -> None:
    print(*args, flush=True)


class DesktopController(QObject):
    stateChanged = Signal()  # connection, active program/profile, last action or pause changed
    audioEvent = Signal()  # a sink/source changed (emitted from the pulse event thread)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paused = False
        self.cfg = DesktopConfig.load()
        # LPD8
        self.programs: dict[int, p.Program] = {}  # as read from the device
        self.active = 0  # program active on the LPD8 (0 = unknown)
        self._announced = 0
        self.pad_state = [False] * p.NUM_PADS
        self._last_leds: list | None = None
        self._last_hint = 0.0
        # Generic devices: active profile index per device (runtime state)
        self.profile_index: dict[str, int] = {}
        self._status: dict = {"pid": os.getpid(), "active": 0, "last": None, "devices": {}}

        self.obs = ObsClient(self)
        self.obs.stateChanged.connect(self._sync_obs_state)
        self.obs.connectedChanged.connect(lambda ok: log("OBS", "connected" if ok else "disconnected"))
        self.focus = FocusTracker(self)
        self.focus.changed.connect(self._on_focus)
        self.runner = ActionRunner(
            self.obs,
            osd=lambda: self.cfg.osd,
            report=self._report,
            audio_changed=self._poll_audio,
            targets=self.focus.snapshot,
            parent=self,
        )

        self.dev = Lpd8Device(self, client_name=CLIENT_NAME)
        self.dev.connectionChanged.connect(self._on_connection)
        self.dev.programReceived.connect(self._on_program)
        self.dev.activeProgramReceived.connect(self._on_active)
        self.dev.noteEvent.connect(self._on_note)
        self.dev.ccEvent.connect(self._on_cc)
        self.dev.pcEvent.connect(self._on_pc)

        self.hub = MidiHub(CLIENT_NAME, exclude={LPD8_CLIENT}, parent=self)
        self.hub.message.connect(self._on_generic)
        self.hub.devicesChanged.connect(self._on_devices_changed)

        self._poll_timer = QTimer(self, interval=1000, timeout=self._poll)
        # Mute state for pad lights: pushed by PipeWire events when pulsectl is available,
        # with a slow safety poll; otherwise polled every second.
        self._audio_timer = QTimer(self, interval=1000, timeout=self._poll_audio)
        self._audio_debounce = QTimer(self, singleShot=True, interval=40, timeout=self._poll_audio)
        self.audioEvent.connect(self._audio_debounce.start)
        self._mute_watcher = audio.MuteWatcher(self.audioEvent.emit)
        self._release_timer = QTimer(self, singleShot=True, interval=60, timeout=lambda: self._push_leds(force=True))

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            self.cfg.save()
        self._watcher = QFileSystemWatcher([str(CONFIG_PATH.parent), str(CONFIG_PATH)], self)
        self._reload_timer = QTimer(self, singleShot=True, interval=200, timeout=self._reload)
        self._watcher.fileChanged.connect(lambda _: self._reload_timer.start())
        self._watcher.directoryChanged.connect(lambda _: self._reload_timer.start())

    # --- lifecycle --------------------------------------------------------------

    def start(self) -> None:
        slots = self.cfg.slots_with_actions()
        log(
            f"LPD8 actions on program(s) {slots or 'none yet'}; "
            f"{len(self.cfg.devices)} other device(s) configured; config {CONFIG_PATH}"
        )
        self._configure_obs()
        self.focus.start()
        log("window tracking", "on (KWin script loaded)" if self.focus.working else "unavailable")
        if audio.available() and self._mute_watcher.start():
            self._audio_timer.setInterval(15000)
            log("audio: in-process (pulsectl), mute changes are events")
        else:
            log("audio: wpctl (install python-pulsectl for lower CPU use)")
        self._write_status()
        self.dev.start()
        self.hub.start()
        self._poll_timer.start()
        self._audio_timer.start()

    def stop(self) -> None:
        self._mute_watcher.stop()
        self.focus.stop()
        self.hub.stop()
        self.dev.stop()
        with contextlib.suppress(OSError):
            STATUS_PATH.unlink()

    def _reload(self) -> None:
        if str(CONFIG_PATH) not in self._watcher.files() and CONFIG_PATH.exists():
            self._watcher.addPath(str(CONFIG_PATH))
        new = DesktopConfig.load()
        if new == self.cfg:
            return
        self.cfg = new
        log("config reloaded")
        self._configure_obs()
        self._last_leds = None
        self._poll_audio()
        self._sync_obs_state()
        self._push_leds()
        self._write_status()

    def _configure_obs(self) -> None:
        o = self.cfg.obs
        self.obs.configure(self.cfg.uses_obs(), o.host, o.port, o.password, self.cfg.obs_inputs())

    # --- status file (read by the editor) -------------------------------------------

    def _report(self, text: str) -> None:
        log(text)
        self._write_status(text)

    def _write_status(self, last: str | None = None) -> None:
        self._status["active"] = self.active
        self._status["connected"] = self.dev.connected
        self._status["paused"] = self.paused
        self._status["targets"] = self.focus.snapshot()
        self._status["devices"] = {
            name: {"profile": self.current_profile_index(name), "connected": name in self.hub.devices}
            for name in sorted(set(self.hub.devices) | set(self.cfg.devices))
        }
        if last is not None:
            self._status["last"] = {"text": last, "time": time.time(), "program": self.active}
        try:
            tmp = STATUS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._status))
            tmp.replace(STATUS_PATH)
        except OSError:
            pass
        self.stateChanged.emit()

    def set_paused(self, paused: bool) -> None:
        if paused == self.paused:
            return
        self.paused = paused
        log("paused" if paused else "resumed")
        if self.cfg.osd:
            system.osd_text("input-keyboard-virtual", "Chimp " + ("paused" if paused else "resumed"))
        self._write_status()

    # --- generic devices --------------------------------------------------------------

    @property
    def generic_devices(self) -> list[str]:
        return self.hub.devices

    def current_profile_index(self, name: str) -> int:
        dev = self.cfg.devices.get(name)
        if dev is None:
            return 0
        return min(self.profile_index.get(name, 0), len(dev.profiles) - 1)

    def set_profile(self, name: str, index: int) -> None:
        dev = self.cfg.devices.get(name)
        if dev is None or not dev.profiles:
            return
        index %= len(dev.profiles)
        self.profile_index[name] = index
        prof = dev.profiles[index]
        log(f"{name}: profile {prof.name}")
        if self.cfg.osd:
            system.osd_text("input-keyboard-virtual", f"{name}: {prof.name}")
        self._write_status()

    def _on_focus(self) -> None:
        g = self.focus.game()
        key = (g or {}).get("pid")
        if key != getattr(self, "_last_game_pid", None):
            self._last_game_pid = key
            if g:
                log(f"game: {g['name']} (pid {g['pid']}, via {g['source']})")
        self._write_status()

    def _on_devices_changed(self) -> None:
        log("MIDI devices:", ", ".join(self.hub.devices) or "none")
        self._write_status()

    def _on_generic(self, name: str, msg: list[int]) -> None:
        dev = self.cfg.devices.get(name)
        if dev is None or not dev.enabled or self.paused:
            return
        addr = _address(msg)
        if addr is None:
            return
        idx = self.current_profile_index(name)
        prof = dev.profiles[idx]
        i = prof.find(addr[0], addr[1], addr[2])
        if i is None:
            return
        c = prof.controls[i]
        if c.action.type == "none":
            return
        what = decode(c, msg)
        if what is None:
            return
        who = f"{name} · {c.name}"
        kind, value = what
        if kind == "press":
            if c.action.type == "profile_next":
                self.set_profile(name, idx + 1)
            elif c.action.type == "profile_prev":
                self.set_profile(name, idx - 1)
            else:
                self.runner.press(c.action, who)
        elif kind == "level":
            raw = addr[3]
            self.runner.level((name, idx, i), c.action, float(value), raw, who)
        elif kind == "step":
            self.runner.step((name, idx, i), c.action, int(value), who)

    # --- LPD8 state -------------------------------------------------------------------

    def _on_connection(self, connected: bool, name: str) -> None:
        log("LPD8", "connected" if connected else "disconnected", name)
        self.active, self._last_leds = 0, None
        if connected:
            for slot in range(1, p.NUM_PROGRAMS + 1):
                self.dev.request_program(slot)
            self.dev.request_active_program()
        self._write_status()

    def _on_program(self, slot: int, prog: p.Program) -> None:
        # Also arrives when the editor reads or verifies a program: keep our copy current.
        self.programs[slot] = prog
        if slot == self.active:
            self._last_leds = None
            self._push_leds()

    def _on_active(self, slot: int) -> None:
        if slot == self.active:
            return
        previous, self.active = self.active, slot
        self._last_leds = None
        self.pad_state = [False] * p.NUM_PADS
        log(f"LPD8 switched to program {slot}")
        if previous and self.cfg.osd and slot != self._announced:
            has = self.cfg.actions(slot).active()
            system.osd_text(
                "input-keyboard-virtual" if has else "audio-midi",
                f"LPD8: Program {slot}" + (" · desktop control" if has else " · MIDI only"),
            )
        self._announced = slot
        self._poll_audio()
        self._sync_obs_state()
        self._push_leds()
        self._write_status()

    def _poll(self) -> None:
        if self.dev.connected:
            self.dev.request_active_program()
            missing = [s for s in range(1, p.NUM_PROGRAMS + 1) if s not in self.programs]
            for slot in missing[:1]:
                self.dev.request_program(slot)

    @staticmethod
    def _eff(prog: p.Program, channel: int) -> int:
        return prog.global_channel if channel == p.GLOBAL_CHANNEL else channel

    def _resolve(self, kind: str, channel: int, number: int) -> tuple[int, int] | None:
        """(program slot, pad/knob index) for an incoming LPD8 event, or None.

        If the event's channel/number only exists in one program, that program must be
        active (this also catches program switches before the next poll). Otherwise
        trust the polled active program.
        """
        hits: list[tuple[int, int]] = []
        for slot, prog in self.programs.items():
            items = prog.knobs if kind == "knob" else prog.pads
            attr = "cc" if kind in ("knob", "cc") else kind
            for i, item in enumerate(items):
                if self._eff(prog, item.channel) == channel and getattr(item, attr) == number:
                    hits.append((slot, i))
                    break
        if not hits:
            return None
        slots = {s for s, _ in hits}
        if len(slots) == 1:
            slot = hits[0][0]
            if slot != self.active:
                self._on_active(slot)
            return hits[0]
        for hit in hits:
            if hit[0] == self.active:
                return hit
        return None

    def _maybe_hint(self) -> None:
        """Pressed something on a program with no actions while another program has some."""
        others = self.cfg.slots_with_actions()
        if not others or not self.cfg.osd or self.cfg.actions(self.active or 1).active():
            return
        now = time.monotonic()
        if now - self._last_hint < HINT_INTERVAL_S:
            return
        self._last_hint = now
        target = others[0]
        system.osd_text(
            "input-keyboard-virtual",
            f"LPD8 is on Program {self.active or '?'} (no desktop actions). "
            f"Hold PROGRAM + pad {target} for desktop control",
        )

    # --- LPD8 MIDI events ------------------------------------------------------------

    def _pad_event(self, kind: str, channel: int, number: int, pressed: bool) -> None:
        hit = self._resolve(kind, channel, number)
        if hit is None or self.paused:
            return
        slot, i = hit
        if not self.cfg.actions(slot).active():
            if pressed:
                self._maybe_hint()
            return
        if pressed:
            self.runner.press(self.cfg.actions(slot).pads[i], f"Pad {i + 1}")
        else:
            self._release_timer.start()

    def _on_note(self, channel: int, note: int, velocity: int) -> None:
        self._pad_event("note", channel, note, velocity > 0)

    def _on_cc(self, channel: int, cc: int, value: int) -> None:
        hit = self._resolve("knob", channel, cc)
        if hit is not None:
            if self.paused:
                return
            slot, k = hit
            a = self.cfg.actions(slot).knobs[k]
            if a.type == "none":
                if not self.cfg.actions(slot).active():
                    self._maybe_hint()
                return
            knob = self.programs[slot].knobs[k] if slot in self.programs else None
            lo, hi = (knob.min, knob.max) if knob else (0, 127)
            frac = (value - lo) / (hi - lo) if hi > lo else value / 127
            self.runner.level(("lpd8", slot, k), a, frac, value, f"Knob {k + 1}")
            return
        self._pad_event("cc", channel, cc, value > 0)

    def _on_pc(self, channel: int, program: int) -> None:
        self._pad_event("pc", channel, program, True)
        self._release_timer.start()

    # --- LPD8 LED feedback -------------------------------------------------------------

    def _active_pads(self):
        return self.cfg.actions(self.active).pads if self.active else []

    def _poll_audio(self) -> None:
        pads = self._active_pads()
        types = {a.type for a in pads}
        if not {"mic_mute", "output_mute"} & types:
            return
        mic = system.is_muted(system.SOURCE) if "mic_mute" in types else None
        out = system.is_muted(system.SINK) if "output_mute" in types else None
        changed = False
        for i, a in enumerate(pads):
            state = {"mic_mute": mic, "output_mute": out}.get(a.type)
            if state is not None and state != self.pad_state[i]:
                self.pad_state[i] = state
                changed = True
        if changed:
            self._push_leds()

    def _sync_obs_state(self) -> None:
        changed = False
        for i, a in enumerate(self._active_pads()):
            if a.type == "obs_record":
                state = self.obs.recording
            elif a.type == "obs_stream":
                state = self.obs.streaming
            elif a.type == "obs_scene":
                state = bool(a.target) and self.obs.scene == a.target
            elif a.type == "obs_mute":
                state = self.obs.muted.get(a.target, False)
            else:
                continue
            state = state and self.obs.connected
            if state != self.pad_state[i]:
                self.pad_state[i] = state
                changed = True
        if changed:
            self._push_leds()

    def _push_leds(self, force: bool = False) -> None:
        if not self.cfg.leds or not self.dev.connected or not self.active:
            return
        prog = self.programs.get(self.active)
        pads = self._active_pads()
        if prog is None or not any(a.type in STATEFUL_PADS for a in pads):
            return
        colours = [
            pad.on_color if (pads[i].type in STATEFUL_PADS and self.pad_state[i]) else pad.off_color
            for i, pad in enumerate(prog.pads)
        ]
        if colours == self._last_leds and not force:
            return
        self._last_leds = colours
        self.dev.set_leds(colours)


def main() -> int:
    from .config import migrate_legacy

    for line in migrate_legacy():
        log("copied settings from the old name:", line)
    if (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")) and "--no-tray" not in sys.argv:
        from PySide6.QtWidgets import QApplication

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setDesktopFileName("chimp")
    else:
        app = QCoreApplication(sys.argv)
    app.setApplicationName("chimpd")
    app.setApplicationDisplayName("Chimp")
    ctl = DesktopController()
    holder: dict = {}
    if type(app) is not QCoreApplication:
        from PySide6.QtWidgets import QSystemTrayIcon

        from .tray import Tray

        # At login the service can start before Plasma's tray: keep trying for a minute.
        attempts = {"n": 0}

        def try_tray() -> None:
            if "tray" in holder:
                return
            if QSystemTrayIcon.isSystemTrayAvailable():
                holder["tray"] = Tray(ctl)
                log("tray icon shown")
            elif attempts["n"] < 30:
                attempts["n"] += 1
                QTimer.singleShot(2000, try_tray)
            else:
                log("no system tray available; running without an icon")

        try_tray()
    ctl.start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    tick = QTimer(interval=250, timeout=lambda: None)  # lets Python see signals
    tick.start()
    app.aboutToQuit.connect(ctl.stop)
    rc = app.exec()
    holder.clear()
    return rc
