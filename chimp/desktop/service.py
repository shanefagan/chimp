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
from PySide6.QtDBus import QDBusConnection, QDBusServiceWatcher
from PySide6.QtGui import QGuiApplication

from .. import protocol as p
from ..device import Lpd8Device
from . import audio, system
from .actions import ActionRunner
from .config import CONFIG_PATH, STATEFUL_PADS, DesktopConfig
from .focus import FocusTracker
from .learn import _address, decode
from .midi import MidiHub
from .obs import ObsClient
from .overlay import OverlayModel

CLIENT_NAME = "Chimp Service"
LOOP_BUS_NAME = "com.performativenonsense.chimp.loop"  # Chimp Loop, while open
LPD8_CLIENT = "LPD8 mk2"
STATUS_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "chimp.json"


def log(*args) -> None:
    print(*args, flush=True)


class DesktopController(QObject):
    looping = False  # Chimp Loop is open (see __init__)
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
        self._informed: set[int] = set()  # programs the user has been told about this session
        # Generic devices: active profile index per device (runtime state)
        self.profile_index: dict[str, int] = {}
        self._status: dict = {"pid": os.getpid(), "active": 0, "last": None, "devices": {}}

        self.obs = ObsClient(self)
        self.obs.stateChanged.connect(self._sync_obs_state)
        self.obs.connectedChanged.connect(lambda ok: log("OBS", "connected" if ok else "disconnected"))
        self.focus = FocusTracker(self)
        self.focus.changed.connect(self._on_focus)
        self.overlay = OverlayModel(self, self)  # window created by main() when there's a display
        self.runner = ActionRunner(
            self.obs,
            osd=lambda: self.cfg.osd,
            report=self._report,
            audio_changed=self._poll_audio,
            targets=self.focus.snapshot,
            feedback=self.overlay.value,
            level_osd=lambda: not self.overlay.enabled(),
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

        # While Chimp Loop is open, the other controllers are drum pads: leave them to it
        # (the LPD8 keeps its desktop program, and the "no actions" hint stays quiet).
        self._loop_watch = QDBusServiceWatcher(
            LOOP_BUS_NAME,
            QDBusConnection.sessionBus(),
            QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration,
            self,
        )
        self._loop_watch.serviceRegistered.connect(lambda _n: self._set_looping(True))
        self._loop_watch.serviceUnregistered.connect(lambda _n: self._set_looping(False))
        with contextlib.suppress(Exception):
            iface = QDBusConnection.sessionBus().interface()
            self.looping = bool(iface and iface.isServiceRegistered(LOOP_BUS_NAME).value())

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
        # LPD8 programs whose pads/knobs drive the desktop (Chimp Loop leaves those alone)
        self._status["desktopPrograms"] = [] if self.paused else self.cfg.slots_with_actions()
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

    def _set_looping(self, on: bool) -> None:
        self.looping = on
        log("Chimp Loop", "opened: other controllers are left to it" if on else "closed")

    def _on_generic(self, name: str, msg: list[int]) -> None:
        dev = self.cfg.devices.get(name)
        if dev is None or not dev.enabled or self.paused or self.looping:
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
        if kind != "release":
            self.overlay.touch_generic(name, idx, i)
        if kind == "press":
            if c.action.type == "profile_next":
                self.set_profile(name, idx + 1)
            elif c.action.type == "profile_prev":
                self.set_profile(name, idx - 1)
            else:
                self.runner.press(c.action, who)
        elif kind == "release":
            self.runner.release(c.action, who)
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
        if previous and slot != self._announced and self.overlay.enabled():
            self._informed.add(slot)
            self.overlay.program_switched(slot)
        elif previous and self.cfg.osd and slot != self._announced:
            self._informed.add(slot)  # the switch popup already says it's MIDI only
            has = self.cfg.actions(slot).active()
            system.osd_text(
                "input-keyboard-virtual" if has else "audio-midi",
                f"LPD8: Program {slot} · " + self.program_use(slot),
            )
        self._announced = slot
        self._poll_audio()
        self._sync_obs_state()
        self._push_leds()
        self._write_status()

    def program_use(self, slot: int) -> str:
        """What an LPD8 program is doing right now: the desktop, Chimp Loop's pads while
        that's open, or just MIDI."""
        if self.cfg.actions(slot).active():
            return "desktop control"
        if self.looping:
            from ..loop import programs

            job = programs.lpd8_jobs(self.cfg.slots_with_actions(), programs.saved_jobs()).get(slot, "")
            if job:
                page = programs.job_page(job)
                what = "roll and glitches" if job == programs.FX else f"sounds {page * 8 + 1}–{page * 8 + 8}"
                return f"Chimp {programs.describe(job)}: {what}"
        return "MIDI only"

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
        """Pressed something on a program with no actions while another program has some.

        Only when we never told the user which program they're on (e.g. the LPD8 was already
        on it when Chimp started), and only once per program per session: playing music on a
        MIDI-only program is normal and mustn't keep popping up a reminder."""
        others = self.cfg.slots_with_actions()
        slot = self.active or 1
        if self.looping or not others or not self.cfg.osd or self.cfg.actions(slot).active() or slot in self._informed:
            return
        self._informed.add(slot)
        target = others[0]
        system.osd_text(
            "input-keyboard-virtual",
            f"LPD8 is on Program {self.active or '?'} (no desktop actions). "
            f"Hold PROGRAM + pad {target} for desktop control",
        )

    # --- LPD8 MIDI events ------------------------------------------------------------

    def _pad_event(self, kind: str, channel: int, number: int, pressed: bool) -> None:
        hit = self._resolve(kind, channel, number)
        if hit is None:
            return
        slot, i = hit
        active = self.cfg.actions(slot).active()
        if not pressed and active:
            # Let go even if Chimp was paused meanwhile, so a hold action can't stay on.
            self.runner.release(self.cfg.actions(slot).pads[i], f"Pad {i + 1}")
        if self.paused:
            return
        if not active:
            if pressed:
                self._maybe_hint()
            return
        if pressed:
            self.overlay.touch_lpd8(slot, ("pad", slot, i))
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
            self.overlay.touch_lpd8(slot, ("lpd8", slot, k))
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
            self.overlay.refresh_if_shown()

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

    if "--version" in sys.argv:
        from ..selfcheck import chimp_version

        print(f"chimpd {chimp_version()}")
        return 0
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
    if isinstance(app, QGuiApplication):  # --no-tray / no display: a plain QCoreApplication
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
        log("overlay", "ready" if ctl.overlay.load() else "unavailable (no layer-shell QML)")
    ctl.start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    tick = QTimer(interval=250, timeout=lambda: None)  # lets Python see signals
    tick.start()
    app.aboutToQuit.connect(ctl.stop)
    rc = app.exec()
    ctl.overlay.unload()
    holder.clear()
    return rc
