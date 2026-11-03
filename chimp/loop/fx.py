"""The master bus: pads' dry mix plus their delay and reverb sends in; one stereo out.

    dry + delay(send) + reverb(send) -> drive -> filter -> compressor -> limiter -> volume

Every stage is numpy over a block (see dsp.py). Master.process splits longer buffers
(exports render 1024 frames at a time) into BLOCK-sized pieces.
"""

from __future__ import annotations

import numpy as np

from . import dsp
from .dsp import BLOCK

# Delay times, in beats
DELAY_TIMES: list[tuple[str, float]] = [
    ("1/16", 0.25),
    ("1/8 triplet", 1 / 3),
    ("1/8", 0.5),
    ("1/8 dotted", 0.75),
    ("1/4", 1.0),
    ("1/4 dotted", 1.5),
    ("1/2", 2.0),
]
REVERB_MODES: list[tuple[str, str]] = [
    ("hall", "Hall"),
    ("rise", "Rise (octave up)"),
    ("sink", "Sink (octave down)"),
    ("reverse", "Reverse"),
]


def _lowpass_kernel(fc: float, rate: int, taps: int) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * fc / rate * n) * np.blackman(taps)
    return h / h.sum()


class FirBank:
    """A short FIR on several channels at once, keeping history between blocks."""

    def __init__(self, kernel: np.ndarray, channels: int) -> None:
        self.channels = channels
        self.set_kernel(kernel)

    def set_kernel(self, kernel: np.ndarray) -> None:
        old = getattr(self, "_hist", None)
        self.kernel = kernel
        self._hist = np.zeros((len(kernel) - 1, self.channels), np.float32)
        if old is not None and len(old) and len(self._hist):
            k = min(len(old), len(self._hist))
            self._hist[-k:] = old[-k:]

    def run(self, x: np.ndarray) -> np.ndarray:
        full = np.concatenate([self._hist, x])
        if len(self._hist):
            self._hist = full[len(x) :].copy()
        cols = [np.convolve(full[:, c], self.kernel, "valid") for c in range(x.shape[1])]
        return np.stack(cols, axis=1).astype(np.float32)


class Delay:
    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.mix = 0.0
        self.feedback = 0.4
        self.time = 3  # index into DELAY_TIMES: dotted eighth
        self.pingpong = True
        self._ring = dsp.Ring(rate * 4, 2)
        self._lp = FirBank(_lowpass_kernel(4500.0, rate, 31), 2)  # each repeat darker

    def run(self, send: np.ndarray, beat_frames: float) -> np.ndarray:
        n = len(send)
        d = max(n + 32, min(int(beat_frames * DELAY_TIMES[self.time][1]), len(self._ring.buf) - n - 1))
        echo = self._ring.read(np.array([d, d]), n)
        back = self._lp.run(echo[:, ::-1] if self.pingpong else echo) * min(self.feedback, 0.95)
        if self.pingpong:  # first repeat on one side, then across
            send = np.stack([send.mean(axis=1), np.zeros(n, np.float32)], axis=1)
        self._ring.write(send + back)
        return echo * self.mix


class Reverb:
    """An 8-line feedback delay network: Hadamard mixing, damping in the loop, slowly
    modulated line lengths. Modes: hall; rise/sink (the tail fed back through an octave
    up/down shifter, so it blooms upward or sinks); reverse (the tail in reversed grains,
    swelling into each hit). Big, dark settings are the point."""

    LINES = np.array([1031, 1327, 1523, 1871, 2099, 2447, 2803, 3251], np.float64)

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.mix = 0.0
        self.size = 0.5  # decay time: 0.6 s .. 30 s
        self.tone = 0.4  # dark .. bright
        self.shimmer = 0.0
        self.mod = 0.3
        self.mode = "hall"
        self._len = self.LINES * rate / 48000
        self._ring = dsp.Ring(8192 + BLOCK * 2, 8)
        h = np.array([[1.0]])
        for _ in range(3):
            h = np.block([[h, h], [h, -h]])
        self._mix_m = (h / np.sqrt(8)).astype(np.float32)
        self._in = np.array([1, -1, 1, -1, 1, -1, 1, -1], np.float32) * 0.35
        out = np.stack([np.array([1, 1, -1, -1, 1, 1, -1, -1]), np.array([1, -1, 1, -1, -1, 1, -1, 1])], 1)
        self._out = (out * 0.3).astype(np.float32)
        self._tone_built: float | None = None
        self._damp = FirBank(np.ones(1), 8)
        self._feed = np.zeros(BLOCK, np.float32)
        self._shift = dsp.Shifter(rate, 0.09)
        self._rev = [dsp.Shifter(rate, 0.35), dsp.Shifter(rate, 0.35)]
        self._t = 0
        self._rates = np.array([0.11, 0.13, 0.17, 0.19, 0.23, 0.29, 0.31, 0.37])
        self._active = False

    def rt60(self) -> float:
        return 0.6 * (50.0**self.size)

    def _update_tone(self) -> None:
        key = round(self.tone, 3)
        if key != self._tone_built:
            fc = 1200.0 * (14000.0 / 1200.0) ** self.tone
            self._damp.set_kernel(_lowpass_kernel(fc, self.rate, 15))
            self._tone_built = key

    def run(self, send: np.ndarray) -> np.ndarray:
        n = len(send)
        if self.mix <= 0.0 and not self._active:
            return np.zeros_like(send)
        self._update_tone()
        t = (self._t + np.arange(n)[:, None]) / self.rate
        self._t += n
        depth = 2 + 28 * self.mod
        delays = self._len[None, :] + depth * (1 + np.sin(2 * np.pi * self._rates[None, :] * t))
        outs = self._ring.read(delays, n)
        gains = (10 ** (-3 * self._len / (self.rt60() * self.rate))).astype(np.float32)
        back = (self._damp.run(outs) @ self._mix_m) * gains[None, :]
        feed = self._feed[:n] if len(self._feed) >= n else np.zeros(n, np.float32)
        mono = send.mean(axis=1) + feed
        self._ring.write(back + mono[:, None] * self._in[None, :])
        wet = outs @ self._out
        if self.mode in ("rise", "sink") and self.shimmer > 0:
            ratio = 2.0 if self.mode == "rise" else 0.5
            self._feed = self._shift.run(wet.mean(axis=1), ratio) * (0.55 * self.shimmer)
        else:
            self._feed = np.zeros(n, np.float32)
        if self.mode == "reverse":
            wet = np.stack([self._rev[c].run(wet[:, c], -1.0) for c in (0, 1)], axis=1)
        self._active = self.mix > 0.0 or float(np.abs(wet).max(initial=0.0)) > 1e-5
        return (wet * self.mix * 1.6).astype(np.float32)


class Master:
    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.delay = Delay(rate)
        self.reverb = Reverb(rate)
        self.filter = dsp.FftFilter(rate)
        self.filter_value = 0.5  # DJ knob
        self.resonance = 0.2
        self.drive_type = "soft"
        self.drive = 0.0
        self.comp = dsp.Compressor(rate)
        self.limiter = dsp.Limiter(rate)
        self.volume = 0.8

    def process(self, dry: np.ndarray, dsend: np.ndarray, rsend: np.ndarray, beat_frames: float) -> np.ndarray:
        if len(dry) > BLOCK:
            parts = range(0, len(dry), BLOCK)
            return np.concatenate(
                [
                    self.process(dry[i : i + BLOCK], dsend[i : i + BLOCK], rsend[i : i + BLOCK], beat_frames)
                    for i in parts
                ]
            )
        x = dry + self.delay.run(dsend, beat_frames) + self.reverb.run(rsend)
        x = dsp.drive(x, self.drive_type, self.drive)
        x = self.filter.process(x, dsp.dj_setting(self.filter_value, self.resonance))
        x = self.comp.run(x)
        return self.limiter.run(x) * self.volume
