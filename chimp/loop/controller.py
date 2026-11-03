"""The Loop window's view of the engine: properties for QML, slots for buttons and keys,
and the MIDI path (pads -> sounds and transport, assign mode)."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..desktop.midi import MidiHub
from . import files
from .engine import QUANTISE_STEPS, Engine
from .kit import NUM_SOUNDS, kit_dirs, load_kit
from .mapping import ACTIONS, LoopSettings, describe, key_of

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

    def start(self) -> None:
        self.hub.start()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hub.stop()
        self._save_settings()

    def _apply_settings(self) -> None:
        s = self.s
        e = self.e
        e.bpm, e.bars, e.metronome, e.volume = float(s.bpm), int(s.bars), bool(s.metronome), float(s.volume)
        e.quantise = s.quantise if s.quantise in QUANTISE_STEPS else 16
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
        try:
            self.s.save()
        except OSError as exc:
            self.notify.emit(f"Couldn't save settings: {exc}")

    # --- MIDI ------------------------------------------------------------------------

    def _on_midi_now(self, device: str, msg: list[int]) -> None:
        """rtmidi's thread: play sounds with no delay. Everything else waits for the UI."""
        if self._assigning:
            return
        k = key_of(msg)
        if k is None:
            return
        target = self.s.target(device, k[0])
        if target and target.startswith("sound:"):
            self.e.trigger(int(target[6:]), k[1], time.monotonic())

    def _on_midi_ui(self, device: str, msg: list[int]) -> None:
        k = key_of(msg)
        if k is None:
            return
        key, vel = k
        if self._assigning:
            what = self._assigning
            self.s.set_assign(device, key, what)
            self._assigning = ""
            label = ACTIONS.get(what[7:]) if what.startswith("action:") else self.padLabels[int(what[6:])]
            self.notify.emit(f"{label} ← {device} · {describe(key)}")
            self._save_settings()
            self.changed.emit()
            return
        target = self.s.target(device, key)
        if not target:
            return
        if target.startswith("sound:"):
            self.padHit.emit(int(target[6:]), vel)
        else:
            self.runAction(target)

    # --- properties ------------------------------------------------------------------

    def _on_tick(self) -> None:
        self.tick.emit()
        if self.e.revision != self._last_rev:
            self._last_rev = self.e.revision
            self.changed.emit()

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
        """[{pos 0-1, sound, layer}] for the timeline, quantised as they'll play."""
        e = self.e
        with e.lock:
            if not e.length:
                return []
            return [
                {"pos": e.placed(h.t) / e.length, "sound": h.sound, "layer": i}
                for i, layer in enumerate(e.layers)
                for h in layer
            ]

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
