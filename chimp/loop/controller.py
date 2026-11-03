"""The Loop window's view of the engine: properties for QML, slots for buttons and keys,
and the MIDI path (pads -> sounds and transport, knobs -> pad or master, assign mode)."""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..desktop.midi import MidiHub
from ..desktop.service import LPD8_CLIENT, STATUS_PATH
from . import dsp, files, voice
from .engine import (
    KNOB_PAGES,
    MASTER_CHOICES,
    MASTER_KNOBS,
    MAX_SWING,
    PRESETS,
    QUANTISE_STEPS,
    STEPS_PER_BAR,
    Engine,
    master_defaults,
)
from .fx import DELAY_TIMES, GLITCHES, REVERB_MODES
from .gamepad import Gamepads
from .kit import NUM_SOUNDS, kit_names
from .kit import load as load_kit
from .mapping import ACTIONS, HOLD_ACTIONS, LoopSettings, describe, key_of, release_of, value_of

QUANTISE_LABELS = {0: "Off", 4: "1/4", 8: "1/8", 16: "1/16", 32: "1/32"}
ROLL_RATES = [(8, "1/8"), (16, "1/16"), (24, "1/16T"), (32, "1/32")]
CHOICE_LABELS = {
    "filter_type": {"lp": "Low-pass", "hp": "High-pass", "bp": "Band-pass"},
    "drive_type": {"soft": "Soft", "hard": "Hard clip", "fuzz": "Fuzz", "crush": "Crush"},
    "reverb_mode": dict(REVERB_MODES),
    "delay_time": {i: name for i, (name, _beats) in enumerate(DELAY_TIMES)},
    "comp_speed": {"slow": "Slow (glue)", "fast": "Fast (punch)"},
}


def _hz(hz: float) -> str:
    return f"{hz / 1000:.1f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"


def _secs(s: float) -> str:
    return f"{s * 1000:.0f} ms" if s < 1 else f"{s:.1f} s"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


class LoopController(QObject):
    changed = Signal()  # settings, kit, devices, assignments, saved loops, grid
    tick = Signal()  # 30 times a second: playhead, state
    padHit = Signal(int, float)  # sound, velocity: for the pad flash
    notify = Signal(str)

    def __init__(self, engine: Engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.e = engine
        self.s = LoopSettings.load()
        self._kits = kit_names()
        self._kit_name = ""
        self._assigning = ""  # "sound:N", "action:name" or "param:p.x" while waiting for a control
        self._taps: list[float] = []
        self._latency_text = "starting audio…"
        self._last_rev = -1
        self._page = 0
        self._follow = True  # the grid page follows the playhead
        self._apply_settings()
        self._held: set[str] = set()  # hold actions down right now (roll, select, mute, solo, g.*)
        # presses a held select/mute/solo already handled on the input thread: the UI skips them
        self._consumed: set[tuple[str, str]] = set()
        self._sound_name = ""
        self.hub = MidiHub("Chimp Loop", realtime=self._on_midi_now)
        self.hub.message.connect(self._on_midi_ui)
        self.hub.devicesChanged.connect(self.changed)
        self.pads = Gamepads(self._input_now)
        self.pads.event.connect(self._input_ui)
        self.pads.devicesChanged.connect(self.changed)
        self._timer = QTimer(self, interval=33, timeout=self._on_tick)
        # The LPD8 on one of its desktop programs belongs to the desktop service: its knobs
        # (CC 70-77 on every program, out of the box) mustn't also turn the looper's.
        self._lpd8_desktop = 0  # that program's number, or 0
        self._status_mtime = 0.0
        self._status_timer = QTimer(self, interval=400, timeout=self._poll_status)
        # Knob sweeps change parameters many times a second: save once they settle.
        self._save_later = QTimer(self, singleShot=True, interval=1500, timeout=self._save_settings)

    def start(self) -> None:
        self._poll_status()
        self.hub.start()
        self.pads.start()
        self._timer.start()
        self._status_timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hub.stop()
        self.pads.stop()
        self._save_settings()
        self.e.close()

    def _apply_settings(self) -> None:
        s = self.s
        e = self.e
        e.bpm, e.bars, e.metronome = float(s.bpm), int(s.bars), bool(s.metronome)
        e.quantise = s.quantise if s.quantise in QUANTISE_STEPS else 16
        e.fixed_velocity = bool(s.fixed_velocity)
        e.knob_page = s.knob_page if s.knob_page in KNOB_PAGES else "pad"
        if s.sound:
            e.apply_sound_settings(s.sound)
        self._load_kit(self._kits.index(s.kit) if s.kit in self._kits else 0)

    def _load_kit(self, index: int) -> None:
        if not self._kits:
            self.notify.emit("No drum kits found")
            return
        name = self._kits[index % len(self._kits)]
        try:
            kit = load_kit(name, self.e.rate)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't load kit {name}: {exc}")
            return
        self.e.set_kit(kit)
        self._kit_name = name
        self.s.kit = name

    def _save_settings(self) -> None:
        with self.e.lock:
            self.s.bpm, self.s.bars, self.s.quantise = self.e.bpm, self.e.bars, self.e.quantise
            self.s.metronome, self.s.volume = self.e.metronome, self.e.volume
            self.s.fixed_velocity, self.s.knob_page = self.e.fixed_velocity, self.e.knob_page
            self.s.sound = self.e.sound_settings()
        try:
            self.s.save()
        except OSError as exc:
            self.notify.emit(f"Couldn't save settings: {exc}")

    # --- the desktop service's LPD8 program ---------------------------------------------

    def _poll_status(self) -> None:
        try:
            mtime = STATUS_PATH.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime == self._status_mtime:
            return
        self._status_mtime = mtime
        busy = 0
        with contextlib.suppress(OSError, ValueError, TypeError):
            st = json.loads(STATUS_PATH.read_text())
            pid = int(st.get("pid", 0))
            alive = pid > 0 and Path(f"/proc/{pid}").exists() and pid != os.getpid()
            active = int(st.get("active") or 0)
            if alive and st.get("connected") and active in (st.get("desktopPrograms") or []):
                busy = active
        if busy != self._lpd8_desktop:
            self._lpd8_desktop = busy
            if busy:
                self.notify.emit(f"LPD8 is on Program {busy} (desktop control): switch program to play drums")
            self.changed.emit()

    @Property(str, notify=changed)
    def lpd8Note(self) -> str:
        n = self._lpd8_desktop
        return f"LPD8 on Program {n} = desktop control (switch program to drum)" if n else ""

    def _ignored(self, device: str) -> bool:
        return bool(self._lpd8_desktop) and device == LPD8_CLIENT

    # --- MIDI ------------------------------------------------------------------------

    def _param_of(self, target: str | None) -> str | None:
        if not target:
            return None
        if target.startswith("knob:"):
            return self.e.knob_target(int(target[5:]))
        if target.startswith("param:"):
            return target[6:]
        return None

    @staticmethod
    def _events(msg: list[int]) -> list[tuple[str, str, float]]:
        """A MIDI message as (kind, key, value) inputs: a value (knob position), a press
        (with velocity), a release."""
        out = []
        v = value_of(msg)
        if v is not None:
            out.append(("value", v[0], v[1]))
        k = key_of(msg)
        if k is not None:
            out.append(("press", k[0], k[1]))
        r = release_of(msg)
        if r is not None:
            out.append(("release", r, 0.0))
        return out

    def _on_midi_now(self, device: str, msg: list[int]) -> None:
        for kind, key, value in self._events(msg):
            if self._input_now(device, kind, key, value):
                return

    def _on_midi_ui(self, device: str, msg: list[int]) -> None:
        for kind, key, value in self._events(msg):
            if self._input_ui(device, kind, key, value):
                return

    def _input_now(self, device: str, kind: str, key: str, value: float) -> bool:
        """MIDI/gamepad thread: anything timing-critical acts here at once (sounds,
        knobs, rolls, glitches). True if the input was a knob (so a CC isn't also a press)."""
        if self._assigning or self._ignored(device):
            return False
        target = self.s.target(device, key)
        if kind == "value":
            pid = self._param_of(target)
            if pid:
                self.e.set_param(pid, value)
                return True
            return False
        if not target or self._param_of(target):
            return False
        if target.startswith("sound:"):
            sound = int(target[6:])
            if kind == "press":
                if self._modify(sound):  # decided now, while the modifier is surely down
                    self._consumed.add((device, key))
                    return False
                if "roll" in self._held:
                    self.e.roll_start(sound, value, time.monotonic())
                else:
                    self.e.trigger(sound, value, time.monotonic())
            else:
                self.e.roll_stop(sound)
        elif target in HOLD_ACTIONS:
            self._hold(target, kind == "press")
        return False

    def _input_ui(self, device: str, kind: str, key: str, value: float) -> bool:
        if self._ignored(device):
            return False
        if self._assigning:
            if self._assigning.startswith("param:"):
                if kind == "value" and self._moved(key, value):
                    self._learn(device, key)
                    return True
            elif kind == "press":
                self._learn(device, key)
                return True
            return False
        target = self.s.target(device, key)
        if kind == "value":
            return bool(self._param_of(target))
        if not target or self._param_of(target):
            return False
        if target.startswith("sound:") and kind == "press":
            if (device, key) in self._consumed:
                self._consumed.discard((device, key))
                self.changed.emit()
            else:
                self.padHit.emit(int(target[6:]), value)
        elif target in HOLD_ACTIONS:
            self.changed.emit()
        elif kind == "press":
            self.runAction(target)
        return False

    @staticmethod
    def _moved(key: str, value: float) -> bool:
        """Clearly moved, not resting jitter: triggers rest at 0, sticks in the middle."""
        if not key.startswith("gpa:"):
            return True
        if key[4:] in ("ABS_Z", "ABS_RZ", "ABS_GAS", "ABS_BRAKE", "ABS_THROTTLE"):
            return value > 0.3
        return abs(value - 0.5) > 0.25

    def _hold(self, name: str, down: bool) -> None:
        if down:
            self._held.add(name)
            if name.startswith("g."):
                self.e.glitch(name[2:])
        else:
            self._held.discard(name)
            if name.startswith("g."):
                self.e.glitch_off(name[2:])
            elif name == "roll":
                for sound in self.e.rolling():
                    self.e.roll_stop(sound)

    def _modify(self, sound: int) -> bool:
        """A pad hit while select/mute/solo is held: do that instead of playing."""
        if "select" in self._held:
            self.e.select(sound)
        elif "mute" in self._held:
            self.e.toggle_mute(sound)
        elif "solo" in self._held:
            self.e.toggle_solo(sound)
        else:
            return False
        return True

    def _learn(self, device: str, key: str) -> None:
        what = self._assigning
        self.s.set_assign(device, key, what)
        self._assigning = ""
        if what.startswith("action:"):
            label = ACTIONS.get(what[7:], what)
        elif what.startswith("param:"):
            label = self._label(what[6:])
        else:
            label = self.padLabels[int(what[6:])]
        self.notify.emit(f"{label} ← {device} · {describe(key)}")
        self._save_settings()
        self.changed.emit()

    def _label(self, pid: str) -> str:
        scope, key = pid.split(".", 1)
        if scope == "m":
            return "Master " + MASTER_KNOBS[key][0].lower()
        return f"Pad {voice.PAD_KNOBS[key][0].lower()}"

    # --- ticking ------------------------------------------------------------------------

    def _on_tick(self) -> None:
        e = self.e
        if self._follow and e.state in ("playing", "overdub") and e.length:
            page = int(e.step_of(e.playhead) // STEPS_PER_BAR)
            if page != self._page and page < self.gridPages:
                self._page = page
                self.changed.emit()
        self.tick.emit()
        if e.revision != self._last_rev:
            self._last_rev = e.revision
            self.changed.emit()
            self._save_later.start()  # knob moves from the controller land here too

    # --- transport and loop properties ---------------------------------------------------

    @Property(str, notify=tick)
    def state(self) -> str:
        return self.e.state

    @Property(str, notify=tick)
    def timeText(self) -> str:
        e = self.e
        r = float(e.rate)
        if e.state == "recording":
            return f"{max(0, e.playhead) / r:4.1f} s"
        if not e.length:
            return ""
        return f"{e.length / r:.2f} s · {e.steps() // STEPS_PER_BAR or 1} bar{'s' if e.steps() > 16 else ''}"

    @Property(int, notify=tick)
    def stepNow(self) -> int:
        """The playing step within the shown page, or -1."""
        e = self.e
        if e.state not in ("playing", "overdub") or not e.length:
            return -1
        k = int(e.playhead / e.step_frames()) % e.steps() - self._page * STEPS_PER_BAR
        return k if 0 <= k < STEPS_PER_BAR else -1

    @Property(float, notify=tick)
    def recordProgress(self) -> float:
        e = self.e
        if e.state == "recording" and e.bars:
            return max(0.0, e.playhead / (e.bars * e.bar_frames()))
        return -1.0

    @Property(float, notify=changed)
    def bpm(self) -> float:
        return round(self.e.bpm, 1)

    @Property(int, notify=changed)
    def bars(self) -> int:
        return self.e.bars

    @Property(int, notify=changed)
    def quantise(self) -> int:
        return self.e.quantise

    @Property(str, notify=changed)
    def quantiseLabel(self) -> str:
        return QUANTISE_LABELS.get(self.e.quantise, "?")

    @Property(bool, notify=changed)
    def metronome(self) -> bool:
        return self.e.metronome

    @Property(bool, notify=changed)
    def fixedVelocity(self) -> bool:
        return self.e.fixed_velocity

    @Property(int, notify=changed)
    def layerCount(self) -> int:
        return len([layer for layer in self.e.layers if layer])

    @Property("QStringList", notify=changed)
    def kitNames(self) -> list[str]:
        return list(self._kits)

    @Property(int, notify=changed)
    def kitIndex(self) -> int:
        names = list(self._kits)
        return names.index(self._kit_name) if self._kit_name in names else 0

    @Property("QStringList", notify=changed)
    def padLabels(self) -> list[str]:
        return self.e.kit.labels() if self.e.kit else [""] * NUM_SOUNDS

    @Property(int, notify=changed)
    def selectedSound(self) -> int:
        return self.e.selected

    @Property("QStringList", notify=changed)
    def devices(self) -> list[str]:
        return self.hub.devices + self.pads.names

    @Property(str, notify=changed)
    def latencyText(self) -> str:
        return self._latency_text

    def set_latency(self, frames: int, device: str) -> None:
        self._latency_text = f"{device} · {frames / self.e.rate * 1000:.0f} ms"
        self.changed.emit()

    # --- performance ----------------------------------------------------------------------

    @Property("QVariantList", notify=changed)
    def muted(self) -> list:
        return sorted(self.e.muted)

    @Property("QVariantList", notify=changed)
    def soloed(self) -> list:
        return sorted(self.e.soloed)

    @Property("QStringList", notify=tick)
    def held(self) -> list[str]:
        return sorted(self._held)

    @Property(str, notify=tick)
    def glitchMode(self) -> str:
        return self.e.master.glitch.mode

    @Property("QVariantList", constant=True)
    def glitches(self) -> list:
        keys = {"stutter4": "5", "stutter8": "6", "stutter16": "7", "stutter32": "8", "tapestop": "9",
                "reverse": "0", "half": "-"}  # fmt: skip
        return [{"id": m, "label": label, "key": keys.get(m, "")} for m, label in GLITCHES]

    @Property("QStringList", constant=True)
    def rollRates(self) -> list[str]:
        return [label for _n, label in ROLL_RATES]

    @Property(int, notify=changed)
    def rollRateIndex(self) -> int:
        rates = [n for n, _l in ROLL_RATES]
        return rates.index(self.e.roll_rate) if self.e.roll_rate in rates else 1

    @Slot(int)
    def setRollRate(self, i: int) -> None:
        self.e.roll_rate = ROLL_RATES[max(0, min(i, len(ROLL_RATES) - 1))][0]
        self.changed.emit()

    @Slot(int, str)
    def padDown(self, sound: int, how: str = "") -> None:
        """A pad on screen or the keyboard. how: "" plays (or rolls while roll is held),
        "select" / "mute" / "solo" do that instead."""
        if self._assigning:
            return
        if how == "select":
            self.e.select(sound)
        elif how == "mute":
            self.e.toggle_mute(sound)
        elif how == "solo":
            self.e.toggle_solo(sound)
        elif not self._modify(sound):
            if "roll" in self._held:
                self.e.roll_start(sound, 1.0)
            else:
                self.e.trigger(sound, 1.0)
            self.padHit.emit(sound, 1.0)
        self.changed.emit()

    @Slot(int)
    def padUp(self, sound: int) -> None:
        self.e.roll_stop(sound)

    @Slot(str)
    def holdDown(self, name: str) -> None:
        self._hold(name, True)
        self.changed.emit()

    @Slot(str)
    def holdUp(self, name: str) -> None:
        self._hold(name, False)
        self.changed.emit()

    @Slot(int)
    def toggleMute(self, sound: int) -> None:
        self.e.toggle_mute(sound)

    @Slot(int)
    def toggleSolo(self, sound: int) -> None:
        self.e.toggle_solo(sound)

    # --- saved sounds -----------------------------------------------------------------------

    @Property("QStringList", notify=changed)
    def soundNames(self) -> list[str]:
        return files.sounds()

    @Property(str, notify=changed)
    def soundName(self) -> str:
        return self._sound_name

    @Slot(str, result=bool)
    def saveSound(self, name: str) -> bool:
        if not name.strip():
            return False
        path = files.save_sound(self.e, self._kit_name, name)
        self._sound_name = path.stem
        self.notify.emit(f"Saved sound {path.stem}")
        self.changed.emit()
        return True

    @Slot(str)
    def loadSound(self, name: str) -> None:
        try:
            d = files.read_sound(name)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't open {name}: {exc}")
            return
        names = list(self._kits)
        if d.get("kit") in names and d["kit"] != self._kit_name:
            self._load_kit(names.index(d["kit"]))
        self.e.apply_sound_settings(d.get("sound") or {})
        self._sound_name = name
        self._save_later.start()
        self.changed.emit()

    @Slot(str)
    def deleteSound(self, name: str) -> None:
        files.delete_sound(name)
        if name == self._sound_name:
            self._sound_name = ""
        self.changed.emit()

    def _step_sound(self, d: int) -> None:
        names = files.sounds()
        if not names:
            self.notify.emit("No saved sounds yet")
            return
        i = names.index(self._sound_name) if self._sound_name in names else (-1 if d > 0 else 0)
        name = names[(i + d) % len(names)]
        self.loadSound(name)
        self.notify.emit(f"Sound: {name}")

    @Property("QStringList", notify=changed)
    def gamepads(self) -> list[str]:
        return self.pads.names

    # --- step grid -----------------------------------------------------------------------

    @Property("QVariantList", notify=changed)
    def gridCells(self) -> list:
        return self.e.grid(self._page)

    @Property(int, notify=changed)
    def gridPage(self) -> int:
        return self._page

    @Property(int, notify=changed)
    def gridPages(self) -> int:
        return max(1, -(-self.e.steps() // STEPS_PER_BAR))

    @Property(bool, notify=changed)
    def followPlayhead(self) -> bool:
        return self._follow

    @Slot(int)
    def setGridPage(self, page: int) -> None:
        self._page = max(0, min(page, self.gridPages - 1))
        self._follow = False
        self.changed.emit()

    @Slot(bool)
    def setFollow(self, on: bool) -> None:
        self._follow = on
        self.changed.emit()

    @Slot(int, int)
    def toggleStep(self, sound: int, step: int) -> None:
        self.e.toggle_step(sound, self._page * STEPS_PER_BAR + step)
        self.e.select(sound)

    @Slot(int, int)
    def cycleStepVelocity(self, sound: int, step: int) -> None:
        """Soft (0.4) -> normal (0.8) -> accent (1.0) -> soft; an empty step gets an accent."""
        s = self._page * STEPS_PER_BAR + step
        v = self.e.grid(self._page)[sound][step]
        if v <= 0:
            self.e.toggle_step(sound, s, 1.0)
        else:
            self.e.set_step_velocity(sound, s, 0.8 if v < 0.6 else 1.0 if v < 0.9 else 0.4)

    # --- sound: pad and master parameters -------------------------------------------------

    def _pad_text(self, key: str, v: float, pad: dict) -> str:
        if key in ("level", "drive", "punch", "delay", "reverb"):
            return "Off" if key in ("drive", "punch") and v <= 0 else _pct(v)
        if key == "pan":
            return "C" if abs(v - 0.5) < 0.02 else f"L{(0.5 - v) * 200:.0f}" if v < 0.5 else f"R{(v - 0.5) * 200:.0f}"
        if key == "pitch":
            st = voice.semitones(pad)
            return "0" if st == 0 else f"{st:+g} st"
        if key == "attack":
            return "0" if v <= 0 else _secs(0.5 * v * v)
        if key == "sustain":
            return "Full" if v >= 1 else _secs(1.5 * v * v)
        if key == "decay":
            return "Full" if v >= 1 else _secs((0.008 + 0.8 * v * v) * 6.9)  # to -60 dB
        if key == "cutoff":
            return "Open" if not voice.filter_on(pad) else _hz(dsp.cutoff_hz(v))
        if key == "resonance":
            return f"Q {dsp.resonance_q(v):.1f}"
        return _pct(v)

    def _master_text(self, key: str, v: float) -> str:
        s = self.e.master_settings
        if key == "filter":
            setting = dsp.dj_setting(v, s["resonance"])
            return "Off" if setting is None else ("LP " if setting[0] == "lp" else "HP ") + _hz(setting[1])
        if key in ("delay", "reverb", "shimmer", "drive", "comp"):
            return "Off" if v <= 0 else _pct(v)
        if key == "size":
            return _secs(self.e.master.reverb.rt60())
        if key == "tone":
            return _hz(1200.0 * (14000.0 / 1200.0) ** v)
        if key == "limit":
            return "Safety" if v <= 0 else f"+{v * 18:.0f} dB"
        if key == "swing":
            return "Off" if v <= 0 else f"{50 * (1 + v * MAX_SWING):.0f}%"
        return _pct(v)

    @Property("QVariantList", notify=changed)
    def padParams(self) -> list:
        pad = self.e.pads[self.e.selected]
        got = self.s.params_assigned()
        page = KNOB_PAGES[self.e.knob_page]
        out = []
        for key, (label, default) in voice.PAD_KNOBS.items():
            pid = f"p.{key}"
            knob = page.index(pid) + 1 if pid in page else 0
            out.append({
                "id": pid, "label": label, "value": pad[key], "default": default,
                "text": self._pad_text(key, pad[key], pad), "assigned": ", ".join(got.get(pid, [])), "knob": knob,
            })  # fmt: skip
        return out

    @Property("QVariantList", notify=changed)
    def masterParams(self) -> list:
        s = self.e.master_settings
        got = self.s.params_assigned()
        page = KNOB_PAGES[self.e.knob_page]
        out = []
        for key, (label, default) in MASTER_KNOBS.items():
            pid = f"m.{key}"
            knob = page.index(pid) + 1 if pid in page else 0
            out.append({
                "id": pid, "label": label, "value": s[key], "default": default,
                "text": self._master_text(key, s[key]), "assigned": ", ".join(got.get(pid, [])), "knob": knob,
            })  # fmt: skip
        return out

    def _choices(self, scope: str, spec: dict, values: dict) -> list:
        out = []
        for key, (label, _default, options) in spec.items():
            if options == (False, True):
                continue  # switches, not lists
            names = CHOICE_LABELS.get(key, {})
            out.append({
                "id": f"{scope}.{key}", "label": label, "options": [str(names.get(o, o)) for o in options],
                "index": list(options).index(values[key]) if values[key] in options else 0,
            })  # fmt: skip
        return out

    @Property("QVariantList", notify=changed)
    def padChoices(self) -> list:
        return self._choices("p", voice.PAD_CHOICES, self.e.pads[self.e.selected])

    @Property("QVariantList", notify=changed)
    def masterChoices(self) -> list:
        return self._choices("m", MASTER_CHOICES, self.e.master_settings)

    @Property(bool, notify=changed)
    def padReverse(self) -> bool:
        return bool(self.e.pads[self.e.selected]["reverse"])

    @Property(bool, notify=changed)
    def pingPong(self) -> bool:
        return bool(self.e.master_settings["pingpong"])

    @Property("QStringList", constant=True)
    def presets(self) -> list[str]:
        return list(PRESETS)

    @Property(str, notify=changed)
    def knobPage(self) -> str:
        return self.e.knob_page

    @Property(float, notify=tick)
    def compReduction(self) -> float:
        return float(self.e.master.comp.reduction_db + self.e.master.limiter.reduction_db)

    @Slot(str, float)
    def setParam(self, pid: str, v: float) -> None:
        self.e.set_param(pid, v)

    @Slot(str)
    def resetParam(self, pid: str) -> None:
        scope, key = pid.split(".", 1)
        default = voice.PAD_KNOBS[key][1] if scope == "p" else MASTER_KNOBS[key][1]
        if pid == "p.cutoff":
            default = voice.OPEN_CUTOFF[self.e.pads[self.e.selected]["filter_type"]]
        self.e.set_param(pid, default)

    @Slot(str, int)
    def setChoice(self, pid: str, index: int) -> None:
        scope, key = pid.split(".", 1)
        spec = voice.PAD_CHOICES if scope == "p" else MASTER_CHOICES
        options = spec[key][2]
        self.e.set_param(pid, options[max(0, min(index, len(options) - 1))])

    @Slot(str, bool)
    def setSwitch(self, pid: str, on: bool) -> None:
        self.e.set_param(pid, bool(on))

    @Slot(str)
    def applyPreset(self, name: str) -> None:
        self.e.apply_preset(name)
        self.notify.emit(f"Master: {name}")

    @Slot()
    def resetPad(self) -> None:
        i = self.e.selected
        for key, value in voice.default_pad().items():
            self.e.set_pad(i, key, value)

    @Slot()
    def resetMaster(self) -> None:
        keep = {k: self.e.master_settings[k] for k in ("swing", "volume")}
        for key, value in {**master_defaults(), **keep}.items():
            self.e.set_master(key, value)

    @Slot(str)
    def setKnobPage(self, page: str) -> None:
        if page in KNOB_PAGES:
            self.e.knob_page = page
            self.e._changed()
            self._save_settings()

    # --- controller assignments, loops -----------------------------------------------------

    @Property(str, notify=changed)
    def assigning(self) -> str:
        return self._assigning

    @Property(str, notify=changed)
    def assigningLabel(self) -> str:
        a = self._assigning
        if a.startswith("sound:"):
            return self.padLabels[int(a[6:])]
        if a.startswith("action:"):
            return ACTIONS.get(a[7:], a)
        if a.startswith("param:"):
            return self._label(a[6:])
        return ""

    @Property("QVariantList", notify=changed)
    def actionList(self) -> list:
        got = self.s.actions_assigned()
        return [{"id": a, "label": label, "assigned": ", ".join(got.get(a, []))} for a, label in ACTIONS.items()]

    @Property("QStringList", notify=changed)
    def savedLoops(self) -> list[str]:
        return files.saved()

    @Property(str, constant=True)
    def exportDir(self) -> str:
        return str(files.EXPORT_DIR)

    # --- slots -----------------------------------------------------------------------------

    @Slot(int)
    def hitPad(self, sound: int) -> None:
        if self._assigning:
            return
        self.e.trigger(sound, 1.0)
        self.padHit.emit(sound, 1.0)

    @Slot(int)
    def selectPad(self, sound: int) -> None:
        self.e.select(sound)

    @Slot(str)
    def runAction(self, action: str) -> None:
        e = self.e
        if action == "record":
            e.record()
        elif action == "play":
            e.play_stop()
            if e.state == "playing":
                self._follow = True
        elif action == "undo":
            e.undo()
        elif action == "clear":
            e.clear()
            self._page = 0
        elif action == "quantise":
            i = QUANTISE_STEPS.index(e.quantise) if e.quantise in QUANTISE_STEPS else 0
            self.setQuantise(QUANTISE_STEPS[(i + 1) % len(QUANTISE_STEPS)])
            self.notify.emit(f"Quantise {self.quantiseLabel}")
        elif action == "metronome":
            self.setMetronome(not e.metronome)
        elif action == "kit":
            self.setKit(self.kitIndex + 1)
            self.notify.emit(f"Kit: {self._kit_name}")
        elif action == "page":
            self.setKnobPage("master" if e.knob_page == "pad" else "pad")
            self.notify.emit(f"Knobs: {e.knob_page}")
        elif action == "sound_next":
            self._step_sound(1)
        elif action == "sound_prev":
            self._step_sound(-1)
        self.changed.emit()

    @Slot(float)
    def setBpm(self, bpm: float) -> None:
        with self.e.lock:
            if self.e.state == "empty":
                self.e.bpm = max(40.0, min(300.0, bpm))
            else:
                # A recorded loop keeps its length; its tempo is what it is.
                self.notify.emit("The tempo of a recorded loop comes from its length: clear it to change")
        self._save_settings()
        self.changed.emit()

    @Slot()
    def tapTempo(self) -> None:
        now = time.monotonic()
        self._taps = [t for t in self._taps if now - t < 2.5] + [now]
        if len(self._taps) >= 3:
            gaps = [b - a for a, b in zip(self._taps, self._taps[1:], strict=False)]
            self.setBpm(60.0 / (sum(gaps) / len(gaps)))

    @Slot(int)
    def setBars(self, bars: int) -> None:
        with self.e.lock:
            if self.e.state == "empty":
                self.e.bars = max(0, bars)
        self._save_settings()
        self.changed.emit()

    @Slot(int)
    def setQuantise(self, q: int) -> None:
        self.e.quantise = q if q in QUANTISE_STEPS else 0
        self._save_settings()
        self.changed.emit()

    @Slot(bool)
    def setMetronome(self, on: bool) -> None:
        self.e.metronome = on
        self._save_settings()
        self.changed.emit()

    @Slot(bool)
    def setFixedVelocity(self, on: bool) -> None:
        self.e.fixed_velocity = on
        self._save_settings()
        self.changed.emit()

    @Slot(int)
    def setKit(self, index: int) -> None:
        self._load_kit(index)
        self._save_settings()
        self.changed.emit()

    @Slot(str)
    def assign(self, target: str) -> None:
        """target 'sound:N', 'action:name' or 'param:p.x': the next control used gets it. '' cancels."""
        self._assigning = target
        self.changed.emit()

    @Slot(str)
    def unassign(self, target: str) -> None:
        self.s.clear_assign(target)
        self._save_settings()
        self.changed.emit()

    @Slot(str, result=bool)
    def saveLoop(self, name: str) -> bool:
        if not self.e.length:
            self.notify.emit("Nothing to save yet")
            return False
        path = files.save(self.e, self._kit_name, name)
        self.notify.emit(f"Saved {path.stem}")
        self.changed.emit()
        return True

    @Slot(str)
    def loadLoop(self, name: str) -> None:
        try:
            d = files.read(name)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't open {name}: {exc}")
            return
        names = list(self._kits)
        if d.get("kit") in names:
            self._load_kit(names.index(d["kit"]))
        files.from_dict(self.e, d)
        self._page = 0
        self.notify.emit(f"Loaded {name} — press Play")
        self.changed.emit()

    @Slot(str)
    def deleteLoop(self, name: str) -> None:
        files.delete(name)
        self.changed.emit()

    @Slot(str, int, result=str)
    def exportWav(self, name: str, repeats: int) -> str:
        return self._export(name, ".wav", lambda p: files.export_wav(self.e, p, repeats))

    @Slot(str, result=str)
    def exportMidi(self, name: str) -> str:
        return self._export(name, ".mid", lambda p: files.export_midi(self.e, p))

    def _export(self, name: str, ext: str, fn) -> str:
        if not self.e.length:
            self.notify.emit("Nothing to export yet")
            return ""
        path = Path(files.EXPORT_DIR) / f"{files.safe_name(name)}{ext}"
        try:
            fn(path)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Export failed: {exc}")
            return ""
        self.notify.emit(f"Exported {path}")
        return str(path)
