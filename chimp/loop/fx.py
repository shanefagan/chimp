"""Master effects for the looper, in numpy, a block at a time (no per-sample Python).

- DJ filter: one knob, centre = off, left = low-pass sweeping down, right = high-pass
  sweeping up. A linear-phase FIR (63 taps, 0.65 ms at 48 kHz, always in the path so
  turning it on never jumps), crossfaded when the knob moves so sweeps don't click.
- Delay: tempo-synced echoes (a dotted eighth by default), mix and feedback.
- Reverb: Schroeder style, four feedback combs and two all-passes per channel.

Feedback lines are all longer than a block (MAX_BLOCK frames), so each block reads only
the past and is one vectorised step. Chain.process splits longer buffers (exports).
"""

from __future__ import annotations

import numpy as np

MAX_BLOCK = 256
TAPS = 63


def _lowpass(fc: float, rate: int) -> np.ndarray:
    n = np.arange(TAPS) - (TAPS - 1) / 2
    h = np.sinc(2 * fc / rate * n) * np.blackman(TAPS)
    return h / h.sum()


def _delta() -> np.ndarray:
    h = np.zeros(TAPS)
    h[(TAPS - 1) // 2] = 1.0
    return h


class FirFilter:
    def __init__(self, rate: int, kernel: np.ndarray | None = None) -> None:
        self.rate = rate
        self.kernel = _delta() if kernel is None else kernel
        self._hist = np.zeros((TAPS - 1, 2), np.float32)

    def process(self, x: np.ndarray, new_kernel: np.ndarray | None = None) -> np.ndarray:
        full = np.concatenate([self._hist, x])
        self._hist = full[-(TAPS - 1) :].copy()
        out = self._conv(full, self.kernel)
        if new_kernel is not None:
            ramp = np.linspace(0.0, 1.0, len(x), dtype=np.float32)[:, None]
            out = out * (1 - ramp) + self._conv(full, new_kernel) * ramp
            self.kernel = new_kernel
        return out

    @staticmethod
    def _conv(full: np.ndarray, k: np.ndarray) -> np.ndarray:
        return np.stack([np.convolve(full[:, c], k, "valid") for c in (0, 1)], axis=1).astype(np.float32)


class DjFilter(FirFilter):
    DEAD = 0.03  # around the centre: off

    def __init__(self, rate: int) -> None:
        super().__init__(rate)
        self.value = 0.5
        self._built = 0.5

    def kernel_for(self, v: float) -> np.ndarray:
        if abs(v - 0.5) <= self.DEAD:
            return _delta()
        if v < 0.5:  # low-pass: 20 kHz at the centre down to 150 Hz
            t = (0.5 - self.DEAD - v) / (0.5 - self.DEAD)
            return _lowpass(20000.0 * (150.0 / 20000.0) ** t, self.rate)
        t = (v - 0.5 - self.DEAD) / (0.5 - self.DEAD)  # high-pass: 20 Hz up to 6 kHz
        return _delta() - _lowpass(20.0 * (6000.0 / 20.0) ** t, self.rate)

    def run(self, x: np.ndarray) -> np.ndarray:
        new = None
        if abs(self.value - self._built) > 1e-4:
            new = self.kernel_for(self.value)
            self._built = self.value
        return self.process(x, new)


class _Line:
    """A delay line of `size` frames, stereo; read `delay` frames back."""

    def __init__(self, size: int) -> None:
        self.buf = np.zeros((size, 2), np.float32)
        self.w = 0

    def read(self, delay: int, n: int) -> np.ndarray:
        idx = (self.w - delay + np.arange(n)) % len(self.buf)
        return self.buf[idx]

    def write(self, x: np.ndarray) -> None:
        idx = (self.w + np.arange(len(x))) % len(self.buf)
        self.buf[idx] = x
        self.w = (self.w + len(x)) % len(self.buf)


class Delay:
    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.mix = 0.0
        self.feedback = 0.35
        self.frames = rate // 4
        self._line = _Line(rate * 3)
        self._lp = FirFilter(rate, _lowpass(5000.0, rate))  # echoes get darker

    def run(self, x: np.ndarray) -> np.ndarray:
        d = max(len(x), min(self.frames, len(self._line.buf) - 1))
        echo = self._line.read(d, len(x))
        self._line.write(x + self._lp.process(echo) * min(self.feedback, 0.92))
        return x + echo * self.mix


class Reverb:
    COMBS = (1557, 1617, 1491, 1422)  # Freeverb's, at 44.1 kHz
    ALLPASS = (556, 441)
    SPREAD = 23  # right channel a little longer: width

    def __init__(self, rate: int) -> None:
        s = rate / 44100
        self.mix = 0.0
        self.room = 0.8
        self._combs = [self._pair(round(d * s)) for d in self.COMBS]
        self._aps = [self._pair(round(d * s)) for d in self.ALLPASS]
        self._pre = FirFilter(rate, _lowpass(6000.0, rate))  # damping, before the tank

    def _pair(self, d: int) -> tuple[_Line, np.ndarray]:
        return _Line(d + self.SPREAD + MAX_BLOCK), np.array([d, d + self.SPREAD])

    @staticmethod
    def _read(line: _Line, delays: np.ndarray, n: int) -> np.ndarray:
        return np.stack([line.read(int(delays[c]), n)[:, c] for c in (0, 1)], axis=1)

    def run(self, x: np.ndarray) -> np.ndarray:
        if self.mix <= 0.0 and not getattr(self, "_ringing", False):
            return x
        n = len(x)
        src = self._pre.process(x) * 0.25
        acc = np.zeros_like(x)
        for line, d in self._combs:
            y = self._read(line, d, n)
            line.write(src + y * self.room)
            acc += y
        for line, d in self._aps:
            b = self._read(line, d, n)
            line.write(acc + b * 0.5)
            acc = b - acc
        self._ringing = self.mix > 0.0 or float(np.abs(acc).max(initial=0.0)) > 1e-5
        return x + acc * self.mix


class Chain:
    """Delay, then reverb, then the DJ filter over everything."""

    def __init__(self, rate: int) -> None:
        self.filter = DjFilter(rate)
        self.delay = Delay(rate)
        self.reverb = Reverb(rate)

    def process(self, x: np.ndarray) -> np.ndarray:
        if len(x) > MAX_BLOCK:
            return np.concatenate([self.process(x[i : i + MAX_BLOCK]) for i in range(0, len(x), MAX_BLOCK)])
        return self.filter.run(self.reverb.run(self.delay.run(x)))
