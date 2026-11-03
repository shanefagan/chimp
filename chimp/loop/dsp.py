"""DSP building blocks for the looper: numpy, a block at a time, no per-sample Python.

Two ways round the "IIR filters need a loop" problem:
- offline (a pad's sample, rendered when its settings change): multiply the spectrum by
  the filter's exact frequency response, with room for the tail to ring;
- live (the master bus): the filter's impulse response, truncated, as an FFT
  convolution (overlap-save), crossfaded when it changes.
Feedback structures (delay, reverb) use lines longer than a block, so each block only
reads the past. Dynamics work on 32-frame chunks, with gains interpolated between them.
"""

from __future__ import annotations

import numpy as np

BLOCK = 256
CHUNK = 32  # dynamics resolution: 0.67 ms at 48 kHz

# --- filters -------------------------------------------------------------------------

FILTER_TYPES = ("lp", "hp", "bp")


def biquad(kind: str, freq: float, q: float, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """RBJ cookbook coefficients (b, a), normalised so a[0] = 1."""
    w0 = 2 * np.pi * min(freq, rate * 0.45) / rate
    cw, alpha = np.cos(w0), np.sin(w0) / (2 * q)
    if kind == "hp":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
    elif kind == "bp":
        b = [alpha, 0.0, -alpha]
    else:
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
    a = [1 + alpha, -2 * cw, 1 - alpha]
    return np.array(b) / a[0], np.array(a) / a[0]


def response(b: np.ndarray, a: np.ndarray, n_fft: int) -> np.ndarray:
    z = np.exp(-1j * np.pi * np.arange(n_fft // 2 + 1) / (n_fft // 2))
    return (b[0] + b[1] * z + b[2] * z * z) / (1 + a[1] * z + a[2] * z * z)


def cutoff_hz(v: float) -> float:
    """0-1 knob to 30 Hz - 18 kHz, exponential."""
    return 30.0 * (18000.0 / 30.0) ** max(0.0, min(1.0, v))


def resonance_q(v: float) -> float:
    return 0.707 * (12.0 / 0.707) ** max(0.0, min(1.0, v))


def filter_offline(x: np.ndarray, kind: str, freq: float, q: float, rate: int, poles: int = 2) -> np.ndarray:
    """x filtered (12 dB/oct per biquad; poles=2: 24 dB/oct), with its tail."""
    tail = int(rate * min(1.0, 0.05 + q / (np.pi * max(freq, 20.0)) * 6))
    n = 1 << int(np.ceil(np.log2(len(x) + tail)))
    b, a = biquad(kind, freq, q, rate)
    h = response(b, a, n) ** poles
    y = np.fft.irfft(np.fft.rfft(x, n, axis=0) * h[:, None], n, axis=0)[: len(x) + tail]
    return y.astype(np.float32)


class FftFilter:
    """Live resonant filter: overlap-save convolution with the biquad's impulse response
    (N_FFT - BLOCK frames of it, faded out), crossfaded block-wise when it changes."""

    N_FFT = 8192

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self._hist = np.zeros((self.N_FFT, 2), np.float32)
        self._k: np.ndarray | None = None  # None = straight through
        self._key: tuple | None = None

    def kernel(self, kind: str, freq: float, q: float) -> np.ndarray:
        b, a = biquad(kind, freq, q, self.rate)
        ir = np.fft.irfft(response(b, a, self.N_FFT) ** 2)  # 24 dB/oct
        keep = self.N_FFT - BLOCK
        ir[keep:] = 0
        ir[keep - 512 : keep] *= np.linspace(1, 0, 512)
        return np.fft.rfft(ir)

    def process(self, x: np.ndarray, setting: tuple | None) -> np.ndarray:
        """setting: (kind, freq, q) or None for off."""
        n = len(x)
        self._hist = np.concatenate([self._hist[n:], x])
        new_k = self._k
        if setting != self._key:
            new_k = None if setting is None else self.kernel(*setting)
            self._key = setting
        if self._k is None and new_k is None:
            return x
        spec = np.fft.rfft(self._hist, axis=0)

        def run(k):
            return x if k is None else np.fft.irfft(spec * k[:, None], self.N_FFT, axis=0)[-n:].astype(np.float32)

        out = run(self._k)
        if new_k is not self._k:
            ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
            out = out * (1 - ramp) + run(new_k) * ramp
            self._k = new_k
        return out


def dj_setting(v: float, resonance: float) -> tuple | None:
    """One knob: centre off, left low-pass sweeping down, right high-pass sweeping up."""
    dead = 0.03
    if abs(v - 0.5) <= dead:
        return None
    t = (abs(v - 0.5) - dead) / (0.5 - dead)
    q = resonance_q(resonance * 0.75)  # keep the master sweep musical
    if v < 0.5:
        return ("lp", 18000.0 * (80.0 / 18000.0) ** t, q)
    return ("hp", 25.0 * (8000.0 / 25.0) ** t, q)


# --- distortion ----------------------------------------------------------------------

DRIVE_TYPES = ("soft", "hard", "fuzz", "crush")


def drive(x: np.ndarray, kind: str, amount: float) -> np.ndarray:
    """amount 0-1; level roughly kept (more drive = more density, not just louder)."""
    if amount <= 0.0:
        return x
    if kind == "crush":
        bits = 16 - amount * 13
        step = 2.0 / (2**bits)
        y = np.round(x / step) * step
        hold = 1 + int(amount * 24)  # sample-rate reduction
        if hold > 1:
            y = np.repeat(y[::hold], hold, axis=0)[: len(x)]
        return y.astype(np.float32)
    g = 10 ** (amount * 1.8)  # up to ~63x
    if kind == "hard":
        y = np.clip(x * g, -1, 1)
    elif kind == "fuzz":  # asymmetric, gated-sounding: the doom/djent kind
        y = np.where(x > 0, np.tanh(x * g), np.tanh(x * g * 0.5) * 0.7)
        y = y + 0.25 * amount * np.abs(y)  # even harmonics
    else:
        y = np.tanh(x * g)
    return (y / (1.0 + amount * 1.5)).astype(np.float32)


# --- envelopes and dynamics ---------------------------------------------------------


def envelope(n: int, rate: int, attack: float, sustain: float, decay: float) -> np.ndarray | None:
    """Gain curve for a pad: rise over `attack`, hold for `sustain`, fall over `decay`.
    Knobs 0-1; sustain 1 and decay 1 = the sample's own shape (None if nothing to do)."""
    if attack <= 0.0 and sustain >= 1.0 and decay >= 1.0:
        return None
    t = np.arange(n, dtype=np.float32) / rate
    a = 0.5 * attack**2  # up to 500 ms
    env = np.minimum(1.0, t / a) if a > 0 else np.ones(n, np.float32)
    if sustain < 1.0 or decay < 1.0:
        hold = a + (1.5 * sustain**2 if sustain < 1.0 else 0.03)
        tau = 0.008 + 0.8 * decay**2 if decay < 1.0 else 0.25
        after = np.maximum(0.0, t - hold)
        env = env * np.exp(-after / tau)
    return env.astype(np.float32)


def punch(x: np.ndarray, amount: float, rate: int) -> np.ndarray:
    """Per-pad compression: fast attack, medium release, makeup gain; brings the body and
    tail up against the transient (amount 0-1)."""
    if amount <= 0.0 or not len(x):
        return x
    comp = Compressor(rate)
    comp.amount, comp.speed = amount, "fast"
    out = np.concatenate([comp.run(x[i : i + BLOCK]) for i in range(0, len(x), BLOCK)])
    peak_in, peak_out = np.abs(x).max(), np.abs(out).max()
    return (out * (peak_in / peak_out) if peak_out > 0 else out).astype(np.float32)


def _chunks(x: np.ndarray) -> np.ndarray:
    """Peak of each CHUNK-frame chunk (both channels)."""
    n = len(x) // CHUNK * CHUNK
    head = np.abs(x[:n]).reshape(-1, CHUNK, x.shape[1]).max(axis=(1, 2))
    if n < len(x):
        head = np.append(head, np.abs(x[n:]).max())
    return head


def _apply(x: np.ndarray, g0: float, gains: np.ndarray) -> np.ndarray:
    """Gain ramps from g0 through the per-chunk gains."""
    pts = np.concatenate([[g0], gains])
    pos = np.minimum(np.arange(len(x)) / CHUNK, len(gains))
    g = np.interp(pos, np.arange(len(pts)), pts).astype(np.float32)
    return x * g[:, None]


class Compressor:
    """Feed-forward peak compressor. amount 0-1 sets threshold (0 to -30 dB) and makeup;
    ratio 4:1 (8:1 past 0.7). speed: 'fast' (1 ms / 80 ms) or 'slow' (15 ms / 300 ms)."""

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.amount = 0.0
        self.speed = "slow"
        self._env = 0.0
        self._g = 1.0
        self.reduction_db = 0.0  # for a meter

    def run(self, x: np.ndarray) -> np.ndarray:
        if self.amount <= 0.0:
            self._g, self.reduction_db = 1.0, 0.0
            return x
        att_s, rel_s = (0.001, 0.08) if self.speed == "fast" else (0.015, 0.3)
        per = CHUNK / self.rate
        att, rel = np.exp(-per / att_s), np.exp(-per / rel_s)
        thr_db = -30.0 * self.amount
        ratio = 4.0 if self.amount < 0.7 else 8.0
        makeup_db = -thr_db * (1 - 1 / ratio) * 0.5
        gains = []
        env = self._env
        for p in _chunks(x):  # BLOCK/CHUNK = 8 steps
            env = att * env + (1 - att) * p if p > env else rel * env + (1 - rel) * p
            lvl = 20 * np.log10(max(env, 1e-6))
            over = max(0.0, lvl - thr_db)
            gains.append(10 ** ((makeup_db - over * (1 - 1 / ratio)) / 20))
        self._env = env
        out = _apply(x, self._g, np.array(gains))
        self._g = gains[-1]
        self.reduction_db = -20 * np.log10(max(gains[-1], 1e-6)) + makeup_db
        return out


class Limiter:
    """Brick-wall with one chunk of lookahead (0.67 ms of latency): nothing leaves above
    `ceiling`. `drive` (0-1) pushes up to +18 dB into it, for loudness."""

    def __init__(self, rate: int, ceiling_db: float = -0.3) -> None:
        self.rate = rate
        self.ceiling = 10 ** (ceiling_db / 20)
        self.drive = 0.0
        self._delay = np.zeros((CHUNK, 2), np.float32)
        self._g = 1.0
        self.reduction_db = 0.0

    def run(self, x: np.ndarray) -> np.ndarray:
        x = x * (10 ** (self.drive * 18 / 20))
        full = np.concatenate([self._delay, x])
        self._delay = full[-CHUNK:].copy()
        out_x = full[: len(x)]
        peaks = _chunks(full)
        # chunk k's gain must cover chunk k and the next one (the lookahead)
        need = np.minimum(1.0, self.ceiling / np.maximum(peaks, 1e-9))
        cover = np.minimum(need[:-1], need[1:]) if len(need) > 1 else need
        cover = cover[: -(-len(out_x) // CHUNK)]
        rel = np.exp(-(CHUNK / self.rate) / 0.12)
        gains, g = [], self._g
        for c in cover:
            g = c if c < g else min(c, rel * g + (1 - rel))
            gains.append(g)
        out = _apply(out_x, self._g, np.array(gains))
        self._g = gains[-1]
        self.reduction_db = -20 * np.log10(max(min(gains), 1e-6))
        return np.clip(out, -self.ceiling, self.ceiling)


# --- lines, pitch shifting, reverse ---------------------------------------------------


class Ring:
    """Multi-channel circular buffer; reads at (fractional) delays behind the write head."""

    def __init__(self, size: int, channels: int) -> None:
        self.buf = np.zeros((size, channels), np.float32)
        self.w = 0

    def write(self, x: np.ndarray) -> None:
        idx = (self.w + np.arange(len(x))) % len(self.buf)
        self.buf[idx] = x
        self.w = (self.w + len(x)) % len(self.buf)

    def read(self, delays: np.ndarray, n: int) -> np.ndarray:
        """n frames; frame i is `delays` (per channel, or per frame and channel) behind
        write position w + i, i.e. behind where the next block's frame i will go."""
        size = len(self.buf)
        pos = (self.w + np.arange(n)[:, None] - delays) % size
        i0 = np.floor(pos).astype(np.int64)
        frac = (pos - i0).astype(np.float32)
        cols = np.arange(self.buf.shape[1])[None, :]
        a = self.buf[i0 % size, cols]
        b = self.buf[(i0 + 1) % size, cols]
        return a + (b - a) * frac


class Shifter:
    """Two-head granular pitch shifter on a mono signal: ratio 2 = octave up, 0.5 = down,
    -1 = reversed grains (the swell of a reverse reverb)."""

    def __init__(self, rate: int, window_s: float = 0.08) -> None:
        self.win = int(rate * window_s)
        self.ring = Ring(self.win * 4 + BLOCK * 2, 1)
        self.phase = 0.0

    def run(self, x: np.ndarray, ratio: float) -> np.ndarray:
        n = len(x)
        self.ring.write(x[:, None])
        w = self.win
        d1 = (self.phase + np.arange(n) * (1.0 - ratio)) % w
        self.phase = (self.phase + n * (1.0 - ratio)) % w
        d2 = (d1 + w / 2) % w
        # frame i was just written at w - n + i: read `d` (+1) behind that
        y1 = self.ring.read((d1 + 1 + n)[:, None], n)[:, 0]
        y2 = self.ring.read((d2 + 1 + n)[:, None], n)[:, 0]
        g1 = np.sin(np.pi * d1 / w) ** 2
        return (y1 * g1 + y2 * (1 - g1)).astype(np.float32)
