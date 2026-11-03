"""Per-pad sound: each pad's settings and the sample they make.

The shaping settings (reverse, drive, filter, punch, envelope) are rendered into the
pad's sample once, when they change, by a background thread, so playing a pad costs
nothing extra and a knob sweep never stalls the audio. Level, pan, the delay/reverb sends
and pitch are applied as it plays, so they act at once, pitch even on a sound already
ringing (turn it during a crash for a dive-bomb).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

from . import dsp

# id: (label, default). Knobs are 0-1.
PAD_KNOBS: dict[str, tuple[str, float]] = {
    "level": ("Level", 0.8),
    "pan": ("Pan", 0.5),
    "pitch": ("Pitch", 0.5),
    "attack": ("Attack", 0.0),
    "sustain": ("Sustain", 1.0),
    "decay": ("Decay", 1.0),
    "cutoff": ("Cutoff", 1.0),
    "resonance": ("Resonance", 0.0),
    "drive": ("Drive", 0.0),
    "punch": ("Punch", 0.0),
    "delay": ("Delay send", 1.0),
    "reverb": ("Reverb send", 1.0),
}
PAD_CHOICES: dict[str, tuple[str, object, tuple]] = {
    "filter_type": ("Filter", "lp", dsp.FILTER_TYPES),
    "drive_type": ("Drive", "soft", dsp.DRIVE_TYPES),
    "reverse": ("Reverse", False, (False, True)),
}
MIX_ONLY = {"level", "pan", "delay", "reverb", "pitch"}  # not baked into the sample
PITCH_RANGE = 24  # semitones either way
OPEN_CUTOFF = {"lp": 1.0, "hp": 0.0, "bp": 0.5}


def default_pad() -> dict:
    d: dict = {k: v for k, (_label, v) in PAD_KNOBS.items()}
    d.update({k: v for k, (_label, v, _opts) in PAD_CHOICES.items()})
    return d


def semitones(pad: dict) -> float:
    return round((pad["pitch"] - 0.5) * 2 * PITCH_RANGE * 4) / 4


def filter_on(pad: dict) -> bool:
    kind, c = pad["filter_type"], pad["cutoff"]
    return not ((kind == "lp" and c >= 0.999) or (kind == "hp" and c <= 0.001))


def shaped(pad: dict) -> bool:
    """Anything to render, or is the sample played as it is?"""
    return (
        pad["reverse"]
        or pad["drive"] > 0
        or filter_on(pad)
        or pad["punch"] > 0
        or dsp.envelope(1, 48000, pad["attack"], pad["sustain"], pad["decay"]) is not None
    )


def render(raw: np.ndarray, pad: dict, rate: int) -> np.ndarray:
    x = raw[::-1] if pad["reverse"] else raw
    x = dsp.drive(np.asarray(x, np.float32), pad["drive_type"], pad["drive"])
    if filter_on(pad):
        q = dsp.resonance_q(pad["resonance"])
        x = dsp.filter_offline(x, pad["filter_type"], dsp.cutoff_hz(pad["cutoff"]), q, rate, poles=1)
        x = np.clip(x, -1.5, 1.5)  # a screaming resonance stays a sound, not a speaker test
    x = dsp.punch(x, pad["punch"], rate)
    env = dsp.envelope(len(x), rate, pad["attack"], pad["sustain"], pad["decay"])
    if env is not None:
        x = x * env[:, None]
        quiet = np.flatnonzero(env > 1e-3)
        end = int(quiet[-1]) + 1 if len(quiet) else 64
        x = x[: max(64, end)].copy()
        x[-64:] *= np.linspace(1, 0, 64, dtype=np.float32)[:, None]
    return np.ascontiguousarray(x, dtype=np.float32)


class Builder:
    """Renders pads whose settings changed, on its own thread.

    `job(i)` returns (version, raw, settings) for pad i or None; `done(i, version, data)`
    stores the result. Data None = play the raw sample."""

    def __init__(self, job: Callable[[int], tuple | None], done: Callable, count: int, rate: int) -> None:
        self._job, self._done, self._count, self._rate = job, done, count, rate
        self._wake = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="chimp-loop-pads", daemon=True)
        self._thread.start()

    def kick(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def build_now(self, i: int) -> None:
        job = self._job(i)
        if job is not None:
            version, raw, pad = job
            self._done(i, version, render(raw, pad, self._rate) if shaped(pad) else None)

    def _run(self) -> None:
        while not self._stop:
            self._wake.wait()
            self._wake.clear()
            for i in range(self._count):
                if self._stop:
                    return
                try:
                    self.build_now(i)
                except Exception as exc:  # noqa: BLE001 - a bad setting must not kill the thread
                    print(f"chimp-loop: pad {i + 1} couldn't be rendered: {exc}", flush=True)
