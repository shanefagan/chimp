"""The looper: what's recorded, what's playing, and the mixer that renders it.

No Qt here. `render(n)` is called by the audio output for every block; `trigger()` by
the MIDI callback thread or the UI; everything else by the UI. One lock covers it all.

Timing: a hit is recorded where it was *heard*. The output plays a block `latency`
frames after rendering it, so a pad hit at wall-clock time t lines up with frame
block_playhead + (t - block_time) * rate - latency. Quantise is applied on playback,
not stored, so it can be changed or turned off after recording.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from .fx import Chain
from .kit import NUM_SOUNDS, Kit

# Closing these chokes the open hi-hat, as on a real kit.
OPEN_HAT = 4
CLOSES_HAT = (3, 9)
MAX_VOICES = 48
QUANTISE_STEPS = (0, 4, 8, 16, 32)  # 0 = off; else notes per bar (4 = quarter notes)

# What knobs can turn, 0-1 each: (id, label, default). Pitch and decay act on the
# selected sound (the last pad hit or clicked).
PARAMS: list[tuple[str, str, float]] = [
    ("filter", "Filter", 0.5),
    ("delay", "Delay", 0.0),
    ("feedback", "Echoes", 0.4),
    ("reverb", "Reverb", 0.0),
    ("swing", "Swing", 0.0),
    ("pitch", "Pitch", 0.5),
    ("decay", "Decay", 1.0),
    ("volume", "Volume", 0.8),
]
PARAM_IDS = [p[0] for p in PARAMS]
MAX_SWING = 0.5  # of a grid step: 0.33 is a triplet feel
VARIANT_CACHE = 64


@dataclass
class Hit:
    t: int  # frames from the loop's start, as played (unquantised)
    sound: int
    vel: float
    born: int  # absolute frame it was recorded at (stops a just-played hit repeating)


class _Voice:
    __slots__ = ("data", "delay", "gain", "pos", "slot")

    def __init__(self, data: np.ndarray, gain: float, delay: int, slot: int) -> None:
        self.data, self.gain, self.delay, self.pos, self.slot = data, gain, delay, 0, slot


def _click(rate: int, freq: float, ms: float = 25.0) -> np.ndarray:
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    mono = np.sin(2 * np.pi * freq * t) * np.exp(-t * 180) * 0.5
    return np.ascontiguousarray(np.stack([mono, mono], axis=1), dtype=np.float32)


class Engine:
    """state: "empty" (nothing recorded), "recording" (first take), "playing",
    "overdub" (playing and adding a layer), "stopped" (a loop, not playing)."""

    def __init__(self, rate: int = 44100) -> None:
        self.rate = rate
        self.lock = threading.RLock()
        self.kit: Kit | None = None
        self.volume = 0.8
        self.bpm = 120.0
        self.bars = 0  # 0 = free length (set by when you close the first take)
        self.quantise = 16
        self.metronome = True  # clicks while recording or overdubbing
        self.latency = 0  # frames between rendering and hearing, set by the output
        self.state = "empty"
        self.length = 0
        self.layers: list[list[Hit]] = []
        self.playhead = 0
        self.abs = 0  # frames rendered since start; never wraps
        self.revision = 0  # bumps on every change the UI should redraw for
        self._voices: list[_Voice] = []
        self._pending: list[tuple[int, float]] = []
        self._block_time = time.monotonic()
        self._block_playhead = 0
        self._block_abs = 0
        self._clicks = (_click(rate, 1760.0), _click(rate, 1175.0))  # bar, beat
        self.fx = Chain(rate)
        self.swing = 0.0  # 0-1 of MAX_SWING
        self.pitch = [0.0] * NUM_SOUNDS  # semitones, -12..+12
        self.decay = [1.0] * NUM_SOUNDS  # 1 = the sample's own length
        self.selected = 0  # the sound pitch/decay knobs act on
        self.fixed_velocity = False
        self._variants: dict[tuple, np.ndarray] = {}

    # --- timing helpers ----------------------------------------------------------

    def beat_frames(self) -> float:
        return self.rate * 60.0 / self.bpm

    def bar_frames(self) -> float:
        return self.beat_frames() * 4

    def grid_frames(self) -> float:
        return self.bar_frames() / self.quantise if self.quantise else 0.0

    def placed(self, t: int) -> int:
        """Where a hit plays: snapped to the grid if quantising, inside the loop."""
        g = self.grid_frames()
        if g and self.length:
            k = round(t / g)
            p = k * g
            if self.swing and self.quantise in (8, 16) and k % 2:
                p += self.swing * MAX_SWING * g  # every second step late
            t = round(p)
        return t % self.length if self.length else t

    def _heard_now(self, now: float | None) -> tuple[int, int]:
        """(playhead, absolute frame) of what the player is hearing right now."""
        elapsed = int(((now if now is not None else time.monotonic()) - self._block_time) * self.rate)
        delta = elapsed - self.latency
        return self._block_playhead + delta, self._block_abs + delta

    def _changed(self) -> None:
        self.revision += 1

    # --- playing and recording ----------------------------------------------------

    def set_kit(self, kit: Kit) -> None:
        with self.lock:
            self.kit = kit
            self._voices.clear()
            self._changed()

    def trigger(self, sound: int, vel: float = 1.0, now: float | None = None) -> None:
        with self.lock:
            if not (0 <= sound < NUM_SOUNDS) or self.kit is None or self.kit.sounds[sound] is None:
                return
            vel = 1.0 if self.fixed_velocity else max(0.0, min(1.0, vel))
            self.selected = sound
            self._pending.append((sound, vel))
            if self.state not in ("recording", "overdub"):
                return
            pos, absf = self._heard_now(now)
            if self.state == "recording":
                pos = max(0, pos)
            elif self.length:
                pos %= self.length
            if not self.layers:
                self.layers.append([])
            self.layers[-1].append(Hit(pos, sound, vel, absf))
            self._changed()

    def record(self, now: float | None = None) -> None:
        """The one-button looper: record a first take, close it, then overdub on/off."""
        with self.lock:
            if self.state == "empty":
                # The take starts at what the player hears as they press. During the take
                # the playhead counts frames from there (it starts at about +latency).
                _, start = self._heard_now(now)
                self.layers = [[]]
                self.length = 0
                self.playhead = self.abs - start
                self._block_playhead = self._block_abs - start
                self.state = "recording"
            elif self.state == "recording":
                pos, _ = self._heard_now(now)
                self._close_take(max(1, pos))
            elif self.state == "playing":
                self.layers.append([])
                self.state = "overdub"
            elif self.state == "overdub":
                self.state = "playing"
            elif self.state == "stopped":
                self.layers.append([])
                self.playhead = 0
                self.state = "overdub"
            self._drop_empty_layers(keep_last=self.state in ("recording", "overdub"))
            self._changed()

    def _close_take(self, length: int) -> None:
        if self.bars:
            length = round(self.bars * self.bar_frames())
        else:
            self.bpm = self.guess_bpm(length)
        self.length = length
        self.playhead %= length
        self.state = "playing"

    def guess_bpm(self, length: int) -> float:
        """A free-length loop is a whole number of beats: pick the count (4, 8, 16, 2,
        32, 1) that gives a tempo in a usual range, so the quantise grid fits it."""
        seconds = length / self.rate
        for beats in (4, 8, 16, 2, 32, 1):
            bpm = 60.0 * beats / seconds
            if 70.0 <= bpm < 150.0:
                return bpm
        return max(20.0, min(300.0, 240.0 / seconds))

    def play_stop(self) -> None:
        with self.lock:
            if self.state in ("playing", "overdub"):
                self.state = "stopped"
                self._drop_empty_layers()
            elif self.state == "stopped":
                self.playhead = 0
                self.state = "playing"
            elif self.state == "recording":
                pos, _ = self._heard_now(None)
                self._close_take(max(1, pos))
            self._changed()

    def undo(self) -> None:
        with self.lock:
            if self.state == "recording":
                self.clear()
                return
            self._drop_empty_layers()
            if self.layers:
                self.layers.pop()
            if self.state == "overdub":
                self.layers.append([])
            self._changed()

    def clear(self) -> None:
        with self.lock:
            self.layers = []
            self.length = 0
            self.playhead = 0
            self.state = "empty"
            self._changed()

    def _drop_empty_layers(self, keep_last: bool = False) -> None:
        last = self.layers[-1] if keep_last and self.layers else None
        self.layers = [layer for layer in self.layers if layer or layer is last]

    def hits(self) -> list[Hit]:
        with self.lock:
            return [h for layer in self.layers for h in layer]

    def load(self, length: int, bpm: float, layers: list[list[Hit]]) -> None:
        with self.lock:
            self.length, self.bpm, self.layers = length, bpm, layers
            self.playhead = 0
            self.state = "stopped" if length else "empty"
            self._changed()

    # --- rendering -----------------------------------------------------------------

    def _start(self, sound: int, vel: float, delay: int) -> None:
        kit = self.kit
        s = kit.sounds[sound] if kit else None
        if s is None:
            return
        if sound in CLOSES_HAT:
            self._voices = [v for v in self._voices if v.slot != OPEN_HAT]
        if len(self._voices) >= MAX_VOICES:
            self._voices.pop(0)
        self._voices.append(_Voice(self._variant(sound, s.data), vel, delay, sound))

    def _variant(self, sound: int, data: np.ndarray) -> np.ndarray:
        """The sample with this sound's pitch and decay (cached)."""
        pitch = round(self.pitch[sound] * 4) / 4
        decay = round(self.decay[sound], 2)
        if pitch == 0 and decay >= 1.0:
            return data
        key = (id(data), pitch, decay)
        v = self._variants.get(key)
        if v is None:
            v = data
            if pitch:
                ratio = 2.0 ** (pitch / 12)
                pos = np.arange(0, len(v) - 1, ratio)
                idx = np.arange(len(v))
                v = np.stack([np.interp(pos, idx, v[:, c]) for c in (0, 1)], axis=1).astype(np.float32)
            if decay < 1.0:
                t = np.arange(len(v), dtype=np.float32) / self.rate
                tau = 0.015 + 0.6 * decay**2  # seconds
                env = np.exp(-t / tau)
                cut = int(np.searchsorted(-env, -1e-3))  # where it's inaudible
                v = (v * env[:, None])[: max(64, cut)].copy()
                v[-64:] *= np.linspace(1, 0, 64, dtype=np.float32)[:, None]
            if len(self._variants) >= VARIANT_CACHE:
                self._variants.pop(next(iter(self._variants)))
            self._variants[key] = v
        return v

    # --- knobs ---------------------------------------------------------------------

    def get_param(self, name: str) -> float:
        fx = self.fx
        return {
            "filter": lambda: fx.filter.value,
            "delay": lambda: fx.delay.mix,
            "feedback": lambda: fx.delay.feedback / 0.9,
            "reverb": lambda: fx.reverb.mix,
            "swing": lambda: self.swing,
            "pitch": lambda: (self.pitch[self.selected] + 12) / 24,
            "decay": lambda: self.decay[self.selected],
            "volume": lambda: self.volume,
        }[name]()

    def set_param(self, name: str, v: float) -> None:
        v = max(0.0, min(1.0, float(v)))
        fx = self.fx
        if name == "filter":
            fx.filter.value = v
        elif name == "delay":
            fx.delay.mix = v
        elif name == "feedback":
            fx.delay.feedback = v * 0.9
        elif name == "reverb":
            fx.reverb.mix = v
        elif name == "swing":
            self.swing = v
        elif name == "pitch":
            self.pitch[self.selected] = round((v * 24 - 12) * 4) / 4
        elif name == "decay":
            self.decay[self.selected] = max(0.02, v)
        elif name == "volume":
            self.volume = v
        self._changed()

    def sound_settings(self) -> dict:
        """What makes a loop sound the way it does, besides its hits (saved with it)."""
        return {
            "pitch": list(self.pitch),
            "decay": list(self.decay),
            "swing": self.swing,
            "fx": {p: self.get_param(p) for p in ("filter", "delay", "feedback", "reverb")},
        }

    def apply_sound_settings(self, d: dict) -> None:
        with self.lock:
            if len(d.get("pitch", [])) == NUM_SOUNDS:
                self.pitch = [float(x) for x in d["pitch"]]
            if len(d.get("decay", [])) == NUM_SOUNDS:
                self.decay = [float(x) for x in d["decay"]]
            self.swing = float(d.get("swing", self.swing))
            for k, v in (d.get("fx") or {}).items():
                if k in PARAM_IDS:
                    self.set_param(k, v)
            self._changed()

    def copy_for_render(self) -> Engine:
        """A separate engine with this loop and its sound, for offline export."""
        with self.lock:
            off = Engine(self.rate)
            off.kit, off.volume, off.bpm, off.quantise, off.bars = (
                self.kit,
                self.volume,
                self.bpm,
                self.quantise,
                self.bars,
            )
            off.metronome = False
            off.apply_sound_settings(self.sound_settings())
            off.layers = [[Hit(h.t, h.sound, h.vel, -(10**12)) for h in layer] for layer in self.layers]
            off.length, off.state = self.length, "playing"
            return off

    def _schedule(self, start: int, n: int) -> None:
        """Start the loop's hits (and metronome clicks) that fall in [start, start+n)."""
        length = self.length
        spans = [(start, min(start + n, length), 0)]
        if start + n > length:
            spans.append((0, start + n - length, length - start))
        for layer in self.layers:
            for h in layer:
                p = self.placed(h.t)
                for a, b, off in spans:
                    if a <= p < b:
                        delay = off + p - a
                        # Recorded a moment ago and quantised forward: it was just heard live.
                        if self._block_abs + delay - h.born < max(self.grid_frames(), self.rate * 0.05):
                            continue
                        self._start(h.sound, h.vel, delay)
        if self.metronome and self.state == "overdub":
            self._clicks_in(spans)

    def _clicks_in(self, spans) -> None:
        beat = self.beat_frames()
        for a, b, off in spans:
            k = int(np.ceil(a / beat))
            while k * beat < b:
                p = round(k * beat)
                if a <= p < b:
                    data = self._clicks[0] if k % 4 == 0 else self._clicks[1]
                    self._voices.append(_Voice(data, 1.0, off + p - a, -1))
                k += 1

    def render(self, n: int) -> np.ndarray:
        out = np.zeros((n, 2), dtype=np.float32)
        clicks = np.zeros((n, 2), dtype=np.float32)
        with self.lock:
            self._block_time = time.monotonic()
            self._block_playhead = self.playhead
            self._block_abs = self.abs
            for sound, vel in self._pending:
                self._start(sound, vel, 0)
            self._pending.clear()
            if self.state in ("playing", "overdub") and self.length:
                self._schedule(self.playhead, n)
            elif self.state == "recording" and self.metronome:
                self._clicks_in([(self.playhead, self.playhead + n, 0)])
            voices = list(self._voices)
            self.playhead += n
            self.abs += n
            if self.state == "recording" and self.bars and self.playhead >= self.bars * self.bar_frames():
                self._close_take(round(self.bars * self.bar_frames()))
                self._changed()
            elif self.state in ("playing", "overdub") and self.length:
                self.playhead %= self.length
            elif self.state in ("stopped", "empty"):
                self.playhead = 0
            volume = self.volume
        alive = []
        for v in voices:
            if v.delay >= n:
                v.delay -= n
                alive.append(v)
                continue
            take = v.data[v.pos : v.pos + n - v.delay]
            dest = clicks if v.slot < 0 else out
            dest[v.delay : v.delay + len(take)] += take * v.gain
            v.pos += len(take)
            v.delay = 0
            if v.pos < len(v.data):
                alive.append(v)
        with self.lock:
            # Keep voices started meanwhile (by trigger) and drop finished/choked ones.
            self._voices = [v for v in self._voices if v in alive or v not in voices]
        self.fx.delay.frames = round(self.beat_frames() * 0.75)  # a dotted eighth
        out = self.fx.process(out)
        out += clicks
        out *= volume
        np.clip(out, -1.0, 1.0, out=out)
        return out
