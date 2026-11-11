"""The looper: what's recorded, what's playing, and the mixer that renders it.

No Qt here. `render(n)` is called by the audio output for every block; `trigger()` by
the MIDI callback thread or the UI; everything else by the UI. One lock covers it all.

Timing: a hit is recorded where it was *heard*. The output plays a block `latency`
frames after rendering it, so a pad hit at wall-clock time t lines up with frame
block_playhead + (t - block_time) * rate - latency. Quantise is applied on playback,
not stored, so it can be changed or turned off after recording.

Sound: each pad has its own settings (voice.py: pitch, envelope, filter, drive,
punch, reverse, level, pan, sends); the master bus (fx.py) adds delay, reverb, drive,
filter, compression and limiting over everything. Both are turned with string ids:
"p.<name>" for the selected pad, "m.<name>" for the master.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from . import voice
from .fx import DELAY_TIMES, REVERB_MODES, Master
from .kit import NUM_SOUNDS, Kit

# Closing these chokes the open hi-hat, as on a real kit.
OPEN_HAT = 4
CLOSES_HAT = (3, 9)
MAX_VOICES = 48
QUANTISE_STEPS = (0, 4, 8, 16, 32)  # 0 = off; else notes per bar (4 = quarter notes)
STEPS_PER_BAR = 16
MAX_SWING = 0.5  # of a grid step: 0.33 is a triplet feel

MASTER_KNOBS: dict[str, tuple[str, float]] = {
    "filter": ("Filter", 0.5),
    "resonance": ("Resonance", 0.2),
    "drive": ("Drive", 0.0),
    "delay": ("Delay", 0.0),
    "echoes": ("Echoes", 0.4),
    "reverb": ("Reverb", 0.0),
    "size": ("Size", 0.5),
    "tone": ("Tone", 0.4),
    "shimmer": ("Shimmer", 0.0),
    "comp": ("Compress", 0.0),
    "limit": ("Limit", 0.0),
    "swing": ("Swing", 0.0),
    "volume": ("Volume", 0.8),
}
MASTER_CHOICES: dict[str, tuple[str, object, tuple]] = {
    "drive_type": ("Drive", "soft", voice.dsp.DRIVE_TYPES),
    "delay_time": ("Delay time", 3, tuple(range(len(DELAY_TIMES)))),
    "pingpong": ("Ping-pong", True, (False, True)),
    "reverb_mode": ("Reverb", "hall", tuple(m for m, _ in REVERB_MODES)),
    "comp_speed": ("Compressor", "slow", ("slow", "fast")),
}
# What a controller's eight knobs turn, page by page: between them, every dial.
KNOB_PAGES: dict[str, list[str]] = {
    "pad": ["p.level", "p.pitch", "p.attack", "p.decay", "p.cutoff", "p.resonance", "p.drive", "p.reverb"],
    "pad2": ["p.pan", "p.sustain", "p.punch", "p.delay"],
    "master": ["m.filter", "m.resonance", "m.drive", "m.delay", "m.echoes", "m.reverb", "m.size", "m.comp"],
    "master2": ["m.tone", "m.shimmer", "m.limit", "m.swing", "m.volume"],
}
KNOB_PAGE_LABELS = {"pad": "Pad", "pad2": "Pad 2", "master": "Master", "master2": "Master 2"}
# Starting points for the master section (pads are left as they are).
PRESETS: dict[str, dict] = {
    "Clean": {},
    "Abyss": {
        "reverb_mode": "sink", "reverb": 0.45, "size": 0.78, "tone": 0.22, "shimmer": 0.5,
        "delay": 0.25, "echoes": 0.45, "delay_time": 5, "pingpong": True,
        "drive_type": "fuzz", "drive": 0.12, "comp": 0.45, "comp_speed": "slow", "limit": 0.35,
    },
    "Bloom": {
        "reverb_mode": "rise", "reverb": 0.4, "size": 0.7, "tone": 0.55, "shimmer": 0.6,
        "delay": 0.2, "echoes": 0.35, "delay_time": 3, "comp": 0.3, "limit": 0.2,
    },
    "Reverse swell": {"reverb_mode": "reverse", "reverb": 0.5, "size": 0.62, "tone": 0.35, "comp": 0.3, "limit": 0.2},
    "Crushed": {
        "drive_type": "crush", "drive": 0.45, "comp": 0.7, "comp_speed": "fast", "limit": 0.5,
        "reverb_mode": "hall", "reverb": 0.15, "size": 0.3, "tone": 0.4,
    },
}  # fmt: skip


def master_defaults() -> dict:
    d: dict = {k: v for k, (_label, v) in MASTER_KNOBS.items()}
    d.update({k: v for k, (_label, v, _opts) in MASTER_CHOICES.items()})
    return d


@dataclass
class Hit:
    t: int  # frames from the loop's start, as played (unquantised)
    sound: int
    vel: float
    born: int  # absolute frame it was recorded at (stops a just-played hit repeating)


class _Voice:
    __slots__ = ("data", "delay", "dry", "dsend", "pos", "rsend", "slot")

    def __init__(self, data: np.ndarray, dry: np.ndarray, dsend: np.ndarray, rsend: np.ndarray, delay: int, slot: int):
        self.data, self.dry, self.dsend, self.rsend = data, dry, dsend, rsend
        self.delay, self.pos, self.slot = delay, 0.0, slot

    def take(self, n: int, rate: float) -> np.ndarray:
        """Up to n frames from where it is, at `rate` (pitch, read live each block, so a
        knob turn bends a sound that's already ringing, in steps: on purpose)."""
        if rate == 1.0 and self.pos == int(self.pos):
            p = int(self.pos)
            out = self.data[p : p + n]
            self.pos = p + len(out)
            return out
        last = len(self.data) - 1
        m = min(n, max(0, int((last - self.pos) / rate)))
        if m <= 0:
            self.pos = float(len(self.data))
            return self.data[:0]
        idx = self.pos + np.arange(m) * rate
        i0 = idx.astype(np.int64)
        frac = (idx - i0).astype(np.float32)[:, None]
        a = self.data[i0]
        out = a + (self.data[i0 + 1] - a) * frac
        self.pos += m * rate
        return out


def _click(rate: int, freq: float, ms: float = 25.0) -> np.ndarray:
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    mono = np.sin(2 * np.pi * freq * t) * np.exp(-t * 180) * 0.5
    return np.ascontiguousarray(np.stack([mono, mono], axis=1), dtype=np.float32)


class Engine:
    """state: "empty" (nothing recorded), "recording" (first take), "playing",
    "overdub" (playing and adding a layer), "stopped" (a loop, not playing)."""

    def __init__(self, rate: int = 44100, background: bool = True) -> None:
        self.rate = rate
        self.lock = threading.RLock()
        self.kit: Kit | None = None
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
        self.selected = 0  # the pad "p." settings act on: chosen explicitly, not by playing
        self.muted: set[int] = set()  # parts left out of the pattern's playback
        self.soloed: set[int] = set()  # if any: only these play from the pattern
        self.roll_rate = 16  # notes per bar (24 = sixteenth triplets)
        self._rolls: dict[int, tuple[float, int]] = {}  # sound -> (velocity, abs frame it began)
        self.fixed_velocity = False
        self.knob_page = "pad"
        self.pads = [voice.default_pad() for _ in range(NUM_SOUNDS)]
        self.master_settings = master_defaults()
        self.master = Master(rate)
        self._apply_master()
        self._pad_version = [0] * NUM_SOUNDS
        self._ready: list[tuple[int, np.ndarray | None]] = [(0, None)] * NUM_SOUNDS
        self._voices: list[_Voice] = []
        self._pending: list[tuple[int, float]] = []
        self._block_time = time.monotonic()
        self._block_playhead = 0
        self._block_abs = 0
        self._clicks = (_click(rate, 1760.0), _click(rate, 1175.0))  # bar, beat
        self._builder = voice.Builder(self._job, self._built, NUM_SOUNDS, rate) if background else None

    def close(self) -> None:
        if self._builder:
            self._builder.stop()

    # --- timing helpers ----------------------------------------------------------

    @property
    def volume(self) -> float:
        return self.master_settings["volume"]

    @volume.setter
    def volume(self, v: float) -> None:
        self.set_master("volume", v)

    @property
    def swing(self) -> float:
        return self.master_settings["swing"]

    def beat_frames(self) -> float:
        return self.rate * 60.0 / self.bpm

    def bar_frames(self) -> float:
        return self.beat_frames() * 4

    def grid_frames(self) -> float:
        return self.bar_frames() / self.quantise if self.quantise else 0.0

    def step_frames(self) -> float:
        return self.bar_frames() / STEPS_PER_BAR

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

    # --- pads ----------------------------------------------------------------------

    def set_kit(self, kit: Kit) -> None:
        with self.lock:
            self.kit = kit
            self._voices.clear()
            self._pad_version = [v + 1 for v in self._pad_version]
            self._changed()
        self._rebuild()

    def _job(self, i: int):
        with self.lock:
            s = self.kit.sounds[i] if self.kit else None
            if s is None or self._ready[i][0] == self._pad_version[i]:
                return None
            return self._pad_version[i], s.data, dict(self.pads[i])

    def _built(self, i: int, version: int, data: np.ndarray | None) -> None:
        with self.lock:
            if version >= self._ready[i][0]:
                self._ready[i] = (version, data)

    def _rebuild(self, now: bool = False) -> None:
        if self._builder and not now:
            self._builder.kick()
        else:
            for i in range(NUM_SOUNDS):
                job = self._job(i)
                if job:
                    v, raw, pad = job
                    self._built(i, v, voice.render(raw, pad, self.rate) if voice.shaped(pad) else None)

    def wait_built(self, timeout: float = 5.0) -> bool:
        """Until every pad's sample matches its settings (tests, exports)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if all(self._ready[i][0] == self._pad_version[i] for i in range(NUM_SOUNDS)):
                    return True
            self._rebuild()
            time.sleep(0.005)
        return False

    def set_pad(self, i: int, key: str, value) -> None:
        with self.lock:
            pad = self.pads[i]
            if key in voice.PAD_KNOBS:
                value = max(0.0, min(1.0, float(value)))
            elif key == "filter_type" and value != pad["filter_type"]:
                pad["cutoff"] = voice.OPEN_CUTOFF.get(value, 1.0)  # switching type starts it open
            if pad.get(key) == value:
                return
            pad[key] = value
            if key not in voice.MIX_ONLY:
                self._pad_version[i] += 1
            self._changed()
        if key not in voice.MIX_ONLY:
            self._rebuild()

    def _data(self, i: int) -> np.ndarray | None:
        s = self.kit.sounds[i] if self.kit else None
        if s is None:
            return None
        built = self._ready[i][1]
        if voice.shaped(self.pads[i]):
            return built if built is not None else s.data
        return s.data

    # --- master --------------------------------------------------------------------

    def set_master(self, key: str, value) -> None:
        with self.lock:
            if key in MASTER_KNOBS:
                value = max(0.0, min(1.0, float(value)))
            self.master_settings[key] = value
            self._apply_master()
            self._changed()

    def _apply_master(self) -> None:
        m, s = self.master, self.master_settings
        m.filter_value, m.resonance = s["filter"], s["resonance"]
        m.drive, m.drive_type = s["drive"], s["drive_type"]
        m.delay.mix, m.delay.feedback = s["delay"], s["echoes"] * 0.9
        m.delay.time, m.delay.pingpong = int(s["delay_time"]), bool(s["pingpong"])
        m.reverb.mix, m.reverb.size, m.reverb.tone = s["reverb"], s["size"], s["tone"]
        m.reverb.shimmer, m.reverb.mode = s["shimmer"], s["reverb_mode"]
        m.comp.amount, m.comp.speed = s["comp"], s["comp_speed"]
        m.limiter.drive = s["limit"]
        m.volume = s["volume"]

    def apply_preset(self, name: str) -> None:
        with self.lock:
            keep = {k: self.master_settings[k] for k in ("swing", "volume")}
            self.master_settings = {**master_defaults(), **PRESETS.get(name, {}), **keep}
            self._apply_master()
            self._changed()

    # --- one interface for knobs: "p.<key>" (selected pad) and "m.<key>" ----------------

    def get_param(self, pid: str):
        scope, key = pid.split(".", 1)
        return self.pads[self.selected][key] if scope == "p" else self.master_settings[key]

    def set_param(self, pid: str, value) -> None:
        scope, key = pid.split(".", 1)
        if scope == "p":
            self.set_pad(self.selected, key, value)
        else:
            self.set_master(key, value)

    def knob_target(self, k: int) -> str | None:
        """What knob k (0-7) turns on the current page (None: nothing on this page)."""
        page = KNOB_PAGES[self.knob_page]
        return page[k] if 0 <= k < len(page) else None

    def sound_settings(self) -> dict:
        """Everything about how the loop sounds, besides its hits (saved with it)."""
        with self.lock:
            return {"pads": [dict(p) for p in self.pads], "master": dict(self.master_settings)}

    def apply_sound_settings(self, d: dict) -> None:
        with self.lock:
            pads = d.get("pads")
            if isinstance(pads, list) and len(pads) == NUM_SOUNDS:
                for i, p in enumerate(pads):
                    fresh = voice.default_pad()
                    fresh.update({k: v for k, v in p.items() if k in fresh})
                    self.pads[i] = fresh
                    self._pad_version[i] += 1
            master = d.get("master")
            if isinstance(master, dict):
                fresh = master_defaults()
                fresh.update({k: v for k, v in master.items() if k in fresh})
                self.master_settings = fresh
                self._apply_master()
            self._changed()
        self._rebuild()

    def copy_for_render(self) -> Engine:
        """A separate engine with this loop and its sound, for offline export."""
        with self.lock:
            off = Engine(self.rate, background=False)
            off.kit, off.bpm, off.quantise, off.bars = self.kit, self.bpm, self.quantise, self.bars
            off.metronome = False
            off.apply_sound_settings(self.sound_settings())
            off.layers = [[Hit(h.t, h.sound, h.vel, -(10**12)) for h in layer] for layer in self.layers]
            off.length, off.state = self.length, "playing"
        off._rebuild(now=True)
        return off

    # --- playing and recording ----------------------------------------------------

    def select(self, sound: int) -> None:
        with self.lock:
            if 0 <= sound < NUM_SOUNDS and sound != self.selected:
                self.selected = sound
                self._changed()

    def trigger(self, sound: int, vel: float = 1.0, now: float | None = None) -> None:
        with self.lock:
            if not (0 <= sound < NUM_SOUNDS) or self.kit is None or self.kit.sounds[sound] is None:
                return
            vel = 1.0 if self.fixed_velocity else max(0.0, min(1.0, vel))
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

    # --- performance: mute/solo, rolls, glitches --------------------------------------

    def toggle_mute(self, sound: int) -> None:
        with self.lock:
            self.muted ^= {sound}
            self._changed()

    def toggle_solo(self, sound: int) -> None:
        with self.lock:
            self.soloed ^= {sound}
            self._changed()

    def _audible(self, sound: int) -> bool:
        return sound not in self.muted and (not self.soloed or sound in self.soloed)

    def roll_start(self, sound: int, vel: float = 1.0, now: float | None = None) -> None:
        """Hold to repeat `sound` at roll_rate, on the grid (recorded like any hit)."""
        with self.lock:
            if not (0 <= sound < NUM_SOUNDS) or sound in self._rolls:
                return
            _, absf = self._heard_now(now)
            self._rolls[sound] = (1.0 if self.fixed_velocity else vel, absf)
        self.trigger(sound, vel, now)

    def roll_stop(self, sound: int) -> None:
        with self.lock:
            self._rolls.pop(sound, None)

    def rolling(self) -> list[int]:
        return sorted(self._rolls)

    def glitch(self, mode: str) -> None:
        with self.lock:
            self.master.glitch.engage(mode, self.beat_frames())
            self._changed()

    def glitch_off(self, mode: str | None = None) -> None:
        with self.lock:
            self.master.glitch.release(mode)
            self._changed()

    def _roll_hits(self, n: int) -> None:
        """Start the rolls' repeats that fall in this block (and record them)."""
        if not self._rolls:
            return
        period = self.bar_frames() / self.roll_rate
        looping = self.state in ("playing", "overdub", "recording")
        base = self.playhead if looping else self.abs
        k = int(np.ceil(base / period))
        while k * period < base + n:
            offset = round(k * period - base)
            if 0 <= offset < n:
                for sound, (vel, began) in list(self._rolls.items()):
                    if self._block_abs + offset - began < period * 0.5:
                        continue  # the press itself just played
                    self._start(sound, vel, offset)
                    if self.state in ("recording", "overdub"):
                        t = round(k * period)
                        t = t % self.length if self.length else max(0, t)
                        if not self.layers:
                            self.layers.append([])
                        self.layers[-1].append(Hit(t, sound, vel, self._block_abs + offset))
                        self._changed()
            k += 1

    # --- step sequencer -------------------------------------------------------------

    def steps(self) -> int:
        """Steps in the loop (16 a bar); for an empty loop, what it will be."""
        if self.length:
            return max(1, round(self.length / self.step_frames()))
        return max(1, self.bars or 1) * STEPS_PER_BAR

    def step_of(self, t: int) -> int:
        return round(t / self.step_frames()) % self.steps()

    def grid(self, page: int) -> list[list[float]]:
        """[sound][step in this page] -> velocity (0 = empty), a bar (16 steps) per page."""
        with self.lock:
            cells = [[0.0] * STEPS_PER_BAR for _ in range(NUM_SOUNDS)]
            if not self.length:
                return cells
            first = page * STEPS_PER_BAR
            for layer in self.layers:
                for h in layer:
                    k = self.step_of(h.t) - first
                    if 0 <= k < STEPS_PER_BAR:
                        cells[h.sound][k] = max(cells[h.sound][k], h.vel)
            return cells

    def toggle_step(self, sound: int, step: int, vel: float = 0.8) -> None:
        """Step entry, as on a drum machine: a step with this sound on it is cleared,
        an empty one gets a hit. An empty loop becomes `bars` bars (1 if free) first."""
        with self.lock:
            if self.state == "recording":
                return
            if not self.length:
                self.length = round(max(1, self.bars or 1) * self.bar_frames())
                self.layers = []
                self.playhead = 0
                self.state = "stopped"
            found = False
            for layer in self.layers:
                keep = [h for h in layer if not (h.sound == sound and self.step_of(h.t) == step)]
                found = found or len(keep) != len(layer)
                layer[:] = keep
            self._drop_empty_layers(keep_last=self.state == "overdub")
            if not found:
                hit = Hit(round(step * self.step_frames()), sound, vel, -(10**12))
                if self.state == "overdub" and self.layers:
                    self.layers[-1].append(hit)
                else:
                    self.layers.append([hit])  # its own layer: Undo takes it back
            self._changed()

    def set_step_velocity(self, sound: int, step: int, vel: float) -> None:
        with self.lock:
            for layer in self.layers:
                for h in layer:
                    if h.sound == sound and self.step_of(h.t) == step:
                        h.vel = max(0.05, min(1.0, vel))
            self._changed()

    # --- rendering -----------------------------------------------------------------

    def _start(self, sound: int, vel: float, delay: int) -> None:
        data = self._data(sound)
        if data is None:
            return
        if sound in CLOSES_HAT:
            self._voices = [v for v in self._voices if v.slot != OPEN_HAT]
        if len(self._voices) >= MAX_VOICES:
            self._voices.pop(0)
        p = self.pads[sound]
        angle = p["pan"] * np.pi / 2  # constant-power pan
        level = vel * (p["level"] / 0.8) ** 2 * 0.8
        dry = np.array([np.cos(angle), np.sin(angle)], np.float32) * np.float32(level * np.sqrt(2))
        self._voices.append(_Voice(data, dry, dry * p["delay"], dry * p["reverb"], delay, sound))

    def _schedule(self, start: int, n: int) -> None:
        """Start the loop's hits (and metronome clicks) that fall in [start, start+n)."""
        length = self.length
        spans = [(start, min(start + n, length), 0)]
        if start + n > length:
            spans.append((0, start + n - length, length - start))
        for layer in self.layers:
            for h in layer:
                if not self._audible(h.sound):
                    continue
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
        zero = np.zeros(2, np.float32)
        for a, b, off in spans:
            k = int(np.ceil(a / beat))
            while k * beat < b:
                p = round(k * beat)
                if a <= p < b:
                    data = self._clicks[0] if k % 4 == 0 else self._clicks[1]
                    self._voices.append(_Voice(data, np.ones(2, np.float32), zero, zero, off + p - a, -1))
                k += 1

    def render(self, n: int) -> np.ndarray:
        dry = np.zeros((n, 2), dtype=np.float32)
        dsend = np.zeros((n, 2), dtype=np.float32)
        rsend = np.zeros((n, 2), dtype=np.float32)
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
            self._roll_hits(n)
            voices = list(self._voices)
            rates = [1.0 if v.slot < 0 else 2.0 ** (voice.semitones(self.pads[v.slot]) / 12) for v in voices]
            self.playhead += n
            self.abs += n
            if self.state == "recording" and self.bars and self.playhead >= self.bars * self.bar_frames():
                self._close_take(round(self.bars * self.bar_frames()))
                self._changed()
            elif self.state in ("playing", "overdub") and self.length:
                self.playhead %= self.length
            elif self.state in ("stopped", "empty"):
                self.playhead = 0
            beat = self.beat_frames()
        alive = []
        for v, rate in zip(voices, rates, strict=True):
            if v.delay >= n:
                v.delay -= n
                alive.append(v)
                continue
            take = v.take(n - v.delay, rate)
            sl = slice(v.delay, v.delay + len(take))
            if v.slot < 0:
                clicks[sl] += take
            else:
                dry[sl] += take * v.dry
                if v.dsend.any():
                    dsend[sl] += take * v.dsend
                if v.rsend.any():
                    rsend[sl] += take * v.rsend
            v.delay = 0
            if v.pos < len(v.data) - 1:
                alive.append(v)
        with self.lock:
            # Keep voices started meanwhile (by trigger) and drop finished/choked ones.
            self._voices = [v for v in self._voices if v in alive or v not in voices]
        out = self.master.process(dry, dsend, rsend, beat)
        out += clicks * 0.8
        np.clip(out, -1.0, 1.0, out=out)
        return out
