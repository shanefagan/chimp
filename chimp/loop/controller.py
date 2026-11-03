"""The Loop window's view of the engine: properties for QML, slots for buttons and keys,
and the MIDI path (pads -> sounds and transport, assign mode)."""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..desktop.midi import MidiHub
from ..desktop.service import LPD8_CLIENT, STATUS_PATH
from . import files
from .engine import MAX_SWING, PARAMS, QUANTISE_STEPS, Engine
from .kit import NUM_SOUNDS, kit_dirs, load_kit
from .mapping import ACTIONS, LoopSettings, describe, key_of, value_of

QUANTISE_LABELS = {0: "Off", 4: "1/4", 8: "1/8", 16: "1/16", 32: "1/32"}


class LoopController(QObject):
    changed = Signal()  # settings, kit, devices, assignments, saved loops
    tick = Signal()  # 30 times a second: playhead, state
    padHit = Signal(int, float)  # sound, velocity: for the pad flash
    notify = Signal(str)

    def __init__(self, engine: Engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.e = engine
        self.s = LoopSettings.load()
        self._kits = kit_dirs()
        self._kit_name = ""
        self._assigning = ""  # "sound:N" or "action:name" while waiting for a control
        self._taps: list[float] = []
        self._latency_text = "starting audio…"
        self._last_rev = -1
        self._apply_settings()
        self.hub = MidiHub("Chimp Loop", realtime=self._on_midi_now)
        self.hub.message.connect(self._on_midi_ui)
        self.hub.devicesChanged.connect(self.changed)
        self._timer = QTimer(self, interval=33, timeout=self._on_tick)
        # The LPD8 on one of its desktop programs belongs to the desktop service: its knobs
        # (CC 70-77 on every program, out of the box) mustn't also turn the looper's.
        self._lpd8_desktop = 0  # that program's number, or 0
        self._status_mtime = 0.0
        self._status_timer = QTimer(self, interval=400, timeout=self._poll_status)
        # Knob sweeps change parameters many times a second: save once they settle.
        self._save_later = QTimer(self, singleShot=True, interval=1500, timeout=self._save_settings)
        self._save_rev = -1

    def start(self) -> None:
        self._poll_status()
        self.hub.start()
        self._timer.start()
        self._status_timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hub.stop()
        self._save_settings()

    def _apply_settings(self) -> None:
        s = self.s
        e = self.e
        e.bpm, e.bars, e.metronome, e.volume = float(s.bpm), int(s.bars), bool(s.metronome), float(s.volume)
        e.quantise = s.quantise if s.quantise in QUANTISE_STEPS else 16
        e.fixed_velocity = bool(s.fixed_velocity)
        if s.sound:
            e.apply_sound_settings(s.sound)
        names = [d.name for d in self._kits]
        self._load_kit(names.index(s.kit) if s.kit in names else 0)

    def _load_kit(self, index: int) -> None:
        if not self._kits:
            self.notify.emit("No drum kits found")
            return
        d = self._kits[index % len(self._kits)]
        self.e.set_kit(load_kit(d, self.e.rate))
        self._kit_name = d.name
        self.s.kit = d.name

    def _save_settings(self) -> None:
        with self.e.lock:
            self.s.bpm, self.s.bars, self.s.quantise = self.e.bpm, self.e.bars, self.e.quantise
            self.s.metronome, self.s.volume = self.e.metronome, self.e.volume
            self.s.fixed_velocity = self.e.fixed_velocity
            self.s.sound = self.e.sound_settings()
        try:
            self.s.save()
        except OSError as exc:
            self.notify.emit(f"Couldn't save settings: {exc}")

    # --- MIDI ------------------------------------------------------------------------

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

    def _on_midi_now(self, device: str, msg: list[int]) -> None:
        """rtmidi's thread: sounds and knobs act at once. Everything else waits for the UI."""
        if self._assigning or self._ignored(device):
            return
        v = value_of(msg)
        if v is not None:
            target = self.s.target(device, v[0])
            if target and target.startswith("param:"):
                self.e.set_param(target[6:], v[1])
                return
        k = key_of(msg)
        if k is None:
            return
        target = self.s.target(device, k[0])
        if target and target.startswith("sound:"):
            self.e.trigger(int(target[6:]), k[1], time.monotonic())

    def _on_midi_ui(self, device: str, msg: list[int]) -> None:
        if self._ignored(device):
            return
        if self._assigning.startswith("param:"):
            v = value_of(msg)
            if v is not None:  # a knob or fader: learn it
                self._learn(device, v[0])
            return
        k = key_of(msg)
        if k is None:
            return
        key, vel = k
        if self._assigning:
            self._learn(device, key)
            return
        target = self.s.target(device, key)
        if not target or target.startswith("param:"):
            return
        if target.startswith("sound:"):
            self.padHit.emit(int(target[6:]), vel)
        else:
            self.runAction(target)

    def _learn(self, device: str, key: str) -> None:
        what = self._assigning
        self.s.set_assign(device, key, what)
        self._assigning = ""
        if what.startswith("action:"):
            label = ACTIONS.get(what[7:], what)
        elif what.startswith("param:"):
            label = {p: lab for p, lab, _ in PARAMS}.get(what[6:], what)
        else:
            label = self.padLabels[int(what[6:])]
        self.notify.emit(f"{label} ← {device} · {describe(key)}")
        self._save_settings()
        self.changed.emit()

    # --- properties ------------------------------------------------------------------

    def _on_tick(self) -> None:
        self.tick.emit()
        if self.e.revision != self._last_rev:
            self._last_rev = self.e.revision
            self.changed.emit()
            self._save_later.start()  # knob moves from the controller land here too

    @Property(str, notify=tick)
    def state(self) -> str:
        return self.e.state

    @Property(float, notify=tick)
    def position(self) -> float:
        e = self.e
        if e.state == "recording":
            return (e.playhead / (e.bars * e.bar_frames())) if e.bars else -1.0
        return e.playhead / e.length if e.length and e.state != "stopped" else 0.0

    @Property(str, notify=tick)
    def timeText(self) -> str:
        e = self.e
        r = float(e.rate)
        if e.state == "recording":
            return f"{max(0, e.playhead) / r:4.1f} s"
        if not e.length:
            return ""
        beats = e.length / e.beat_frames()
        return f"{e.length / r:.2f} s · {beats:.0f} beats"

    @Property(int, notify=changed)
    def beats(self) -> int:
        """Beats in the loop (for the timeline's grid); 0 before there is one."""
        e = self.e
        return max(1, round(e.length / e.beat_frames())) if e.length else 0

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

    @Property(float, notify=changed)
    def volume(self) -> float:
        return self.e.volume

    @Property(int, notify=changed)
    def layerCount(self) -> int:
        return len([layer for layer in self.e.layers if layer])

    @Property("QStringList", notify=changed)
    def kitNames(self) -> list[str]:
        return [d.name for d in self._kits]

    @Property(int, notify=changed)
    def kitIndex(self) -> int:
        names = [d.name for d in self._kits]
        return names.index(self._kit_name) if self._kit_name in names else 0

    @Property("QStringList", notify=changed)
    def padLabels(self) -> list[str]:
        return self.e.kit.labels() if self.e.kit else [""] * NUM_SOUNDS

    @Property("QVariantList", notify=changed)
    def hitMarks(self) -> list:
        """[{pos 0-1, sound, layer, vel}] for the timeline, quantised as they'll play."""
        e = self.e
        with e.lock:
            if not e.length:
                return []
            return [
                {"pos": e.placed(h.t) / e.length, "sound": h.sound, "layer": i, "vel": h.vel}
                for i, layer in enumerate(e.layers)
                for h in layer
            ]

    @Property(int, notify=changed)
    def selectedSound(self) -> int:
        return self.e.selected

    @Property(bool, notify=changed)
    def fixedVelocity(self) -> bool:
        return self.e.fixed_velocity

    @Property("QVariantList", notify=changed)
    def params(self) -> list:
        """[{id, label, value 0-1, text, assigned}] for the effect dials."""
        e = self.e
        got = self.s.params_assigned()
        sel = self.padLabels[e.selected] or f"Sound {e.selected + 1}"
        out = []
        for pid, label, _default in PARAMS:
            v = e.get_param(pid)
            if pid in ("pitch", "decay"):
                label = f"{label} · {sel}"
            out.append(
                {
                    "id": pid,
                    "label": label,
                    "value": v,
                    "text": self._param_text(pid, v),
                    "assigned": ", ".join(got.get(pid, [])),
                }
            )
        return out

    def _param_text(self, pid: str, v: float) -> str:
        e = self.e
        if pid == "filter":
            if abs(v - 0.5) <= e.fx.filter.DEAD:
                return "Off"
            t = (abs(v - 0.5) - e.fx.filter.DEAD) / (0.5 - e.fx.filter.DEAD)
            hz = 20000.0 * (150.0 / 20000.0) ** t if v < 0.5 else 20.0 * (6000.0 / 20.0) ** t
            return ("LP " if v < 0.5 else "HP ") + (f"{hz / 1000:.1f} kHz" if hz >= 1000 else f"{hz:.0f} Hz")
        if pid in ("delay", "reverb"):
            return "Off" if v <= 0 else f"{v * 100:.0f}%"
        if pid == "swing":
            return "Off" if v <= 0 else f"{50 * (1 + v * MAX_SWING):.0f}%"
        if pid == "pitch":
            p = e.pitch[e.selected]
            return "0" if p == 0 else f"{p:+g} st"
        if pid == "decay":
            return "Full" if v >= 1 else f"{v * 100:.0f}%"
        return f"{v * 100:.0f}%"

    @Property("QStringList", notify=changed)
    def devices(self) -> list[str]:
        return self.hub.devices

    @Property(str, notify=changed)
    def latencyText(self) -> str:
        return self._latency_text

    def set_latency(self, frames: int, device: str) -> None:
        self._latency_text = f"{device} · {frames / self.e.rate * 1000:.0f} ms"
        self.changed.emit()

    @Property(str, notify=changed)
    def assigning(self) -> str:
        return self._assigning

    @Property("QVariantList", notify=changed)
    def actionList(self) -> list:
        got = self.s.actions_assigned()
        return [{"id": a, "label": label, "assigned": ", ".join(got[a])} for a, label in ACTIONS.items()]

    @Property("QStringList", notify=changed)
    def savedLoops(self) -> list[str]:
        return files.saved()

    @Property(str, constant=True)
    def exportDir(self) -> str:
        return str(files.EXPORT_DIR)

    # --- slots -----------------------------------------------------------------------

    @Slot(int)
    def hitPad(self, sound: int) -> None:
        if self._assigning:
            return
        self.e.trigger(sound, 1.0)
        self.padHit.emit(sound, 1.0)
        self.changed.emit()  # selected sound: pitch/decay dials follow it

    @Slot(str, float)
    def setParam(self, pid: str, v: float) -> None:
        self.e.set_param(pid, v)
        self._save_later.start()

    @Slot(str)
    def resetParam(self, pid: str) -> None:
        self.e.set_param(pid, {p: d for p, _l, d in PARAMS}[pid])
        self._save_later.start()
        self.changed.emit()

    @Slot(bool)
    def setFixedVelocity(self, on: bool) -> None:
        self.e.fixed_velocity = on
        self._save_settings()
        self.changed.emit()

    @Slot(str)
    def runAction(self, action: str) -> None:
        e = self.e
        if action == "record":
            e.record()
        elif action == "play":
            e.play_stop()
        elif action == "undo":
            e.undo()
        elif action == "clear":
            e.clear()
        elif action == "quantise":
            i = QUANTISE_STEPS.index(e.quantise) if e.quantise in QUANTISE_STEPS else 0
            self.setQuantise(QUANTISE_STEPS[(i + 1) % len(QUANTISE_STEPS)])
            self.notify.emit(f"Quantise {self.quantiseLabel}")
        elif action == "metronome":
            self.setMetronome(not e.metronome)
        elif action == "kit":
            self.setKit(self.kitIndex + 1)
            self.notify.emit(f"Kit: {self._kit_name}")
        self.changed.emit()

    @Slot(float)
    def setBpm(self, bpm: float) -> None:
        with self.e.lock:
            if self.e.state == "empty":
                self.e.bpm = max(40.0, min(300.0, bpm))
            else:
                # A recorded free loop keeps its length; its tempo is what it is.
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

    @Slot(float)
    def setVolume(self, v: float) -> None:
        self.e.volume = max(0.0, min(1.0, v))
        self._save_settings()
        self.changed.emit()

    @Slot(int)
    def setKit(self, index: int) -> None:
        self._load_kit(index)
        self._save_settings()
        self.changed.emit()

    @Slot(str)
    def assign(self, target: str) -> None:
        """target 'sound:N' or 'action:name': the next control pressed gets it. '' cancels."""
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
        names = [k.name for k in self._kits]
        if d.get("kit") in names:
            self._load_kit(names.index(d["kit"]))
        files.from_dict(self.e, d)
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
