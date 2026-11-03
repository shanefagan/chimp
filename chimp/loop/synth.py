"""The built-in kit: 16 drum sounds synthesised in numpy at startup (no samples shipped).

Analog-drum-machine recipes: pitch-swept sines for kick and toms, tone plus filtered noise
for snare and clap, six detuned square waves through a high-pass for hats and cymbals,
square-wave cowbell, a claves ping. Shape them per pad like any kit; your own sample kits
go in ~/.local/share/chimp/kits/<name>/.
"""

from __future__ import annotations

import numpy as np

from . import dsp

# The 808's metal: six square waves at awkward ratios
METAL = (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)


def _t(secs: float, rate: int) -> np.ndarray:
    return np.arange(int(secs * rate), dtype=np.float64) / rate


def _env(t: np.ndarray, decay: float, attack: float = 0.0005) -> np.ndarray:
    return np.minimum(1.0, t / attack) * np.exp(-t / decay)


def _sweep(t: np.ndarray, rate: int, low: float, high: float, fall: float) -> np.ndarray:
    freq = low + (high - low) * np.exp(-t / fall)
    return np.sin(2 * np.pi * np.cumsum(freq) / rate)


def _noise(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).uniform(-1, 1, n)


def _filt(x: np.ndarray, kind: str, freq: float, q: float, rate: int) -> np.ndarray:
    y = dsp.filter_offline(np.stack([x, x], axis=1).astype(np.float32), kind, freq, q, rate)
    return y[: len(x), 0].astype(np.float64)


def _metal(t: np.ndarray, scale: float = 1.0) -> np.ndarray:
    return sum(np.sign(np.sin(2 * np.pi * f * scale * t + i)) for i, f in enumerate(METAL)) / len(METAL)


def _stereo(left: np.ndarray, right: np.ndarray | None = None) -> np.ndarray:
    right = left if right is None else right
    x = np.stack([left, right], axis=1)
    peak = np.abs(x).max()
    return (x / peak * 0.9 if peak > 0 else x).astype(np.float32)


def kick(rate: int) -> np.ndarray:
    t = _t(0.9, rate)
    body = _sweep(t, rate, 42, 165, 0.032) * _env(t, 0.42)
    click = _filt(_noise(len(t), 1), "lp", 3500, 0.7, rate) * _env(t, 0.004) * 0.4
    return _stereo(np.tanh(1.8 * (body + click)))


def snare(rate: int, seed: int = 2, tone: float = 185.0, snap: float = 0.18) -> np.ndarray:
    t = _t(0.5, rate)
    body = (np.sin(2 * np.pi * tone * t) + 0.6 * np.sin(2 * np.pi * tone * 1.78 * t)) * _env(t, 0.07)
    wires = _filt(_noise(len(t), seed), "hp", 1400, 0.7, rate) * _env(t, snap)
    return _stereo(np.tanh(1.3 * (0.7 * body + wires)))


def clap(rate: int) -> np.ndarray:
    t = _t(0.6, rate)
    n = _filt(_noise(len(t), 3), "bp", 1100, 1.4, rate)
    bursts = sum(_env(np.maximum(0, t - d), 0.006) * (t >= d) for d in (0.0, 0.011, 0.023))
    tail = _env(np.maximum(0, t - 0.03), 0.16) * (t >= 0.03)
    return _stereo(n * (bursts + 0.8 * tail))


def hat(rate: int, decay: float, level: float = 1.0, seed: int = 4) -> np.ndarray:
    t = _t(max(0.15, decay * 7), rate)
    m = _filt(_metal(t) + 0.3 * _noise(len(t), seed), "hp", 7200, 0.9, rate) * _env(t, decay)
    m2 = _filt(_metal(t, 1.004) + 0.3 * _noise(len(t), seed + 1), "hp", 7200, 0.9, rate) * _env(t, decay)
    x = _stereo(m, m2)
    return x * level


def rim(rate: int) -> np.ndarray:
    t = _t(0.12, rate)
    x = (np.sin(2 * np.pi * 1700 * t) + 0.5 * np.sin(2 * np.pi * 480 * t)) * _env(t, 0.011)
    x += _filt(_noise(len(t), 6), "hp", 3000, 0.7, rate) * _env(t, 0.003) * 0.5
    return _stereo(x)


def tom(rate: int, base: float) -> np.ndarray:
    t = _t(0.9, rate)
    body = _sweep(t, rate, base, base * 1.9, 0.05) * _env(t, 0.32)
    skin = _filt(_noise(len(t), int(base)), "bp", base * 4, 1.0, rate) * _env(t, 0.03) * 0.25
    return _stereo(np.tanh(1.4 * (body + skin)))


def cymbal(rate: int, decay: float, cutoff: float, ping: float | None, seed: int) -> np.ndarray:
    t = _t(decay * 4.5, rate)

    def side(s, scale):
        m = _metal(t, scale) * 0.7 + _noise(len(t), s) * 0.5
        x = _filt(m, "hp", cutoff, 0.8, rate) * _env(t, decay, attack=0.002)
        if ping:
            x += np.sin(2 * np.pi * ping * scale * t) * _env(t, decay * 0.6) * 0.25
        return x

    return _stereo(side(seed, 1.0), side(seed + 1, 1.006))


def cowbell(rate: int) -> np.ndarray:
    t = _t(0.5, rate)
    x = (np.sign(np.sin(2 * np.pi * 540 * t)) + np.sign(np.sin(2 * np.pi * 800 * t))) * 0.5
    x = _filt(x, "bp", 800, 2.0, rate) * (0.6 * _env(t, 0.02) + 0.4 * _env(t, 0.18))
    return _stereo(x)


def tambourine(rate: int) -> np.ndarray:
    t = _t(0.4, rate)
    jingles = _filt(_metal(t, 9.0) * 0.6 + _noise(len(t), 9), "hp", 6000, 1.2, rate)
    shake = sum(_env(np.maximum(0, t - d), 0.05) * (t >= d) for d in (0.0, 0.035, 0.07))
    return _stereo(jingles * shake)


def claves(rate: int) -> np.ndarray:
    t = _t(0.15, rate)
    return _stereo(np.sin(2 * np.pi * 2500 * t) * _env(t, 0.035))


def sounds(rate: int) -> list[np.ndarray]:
    """In kit.SLOTS order."""
    return [
        kick(rate),
        snare(rate),
        clap(rate),
        hat(rate, 0.045),
        hat(rate, 0.42, seed=14),
        rim(rate),
        tom(rate, 190),
        cymbal(rate, 1.3, 4000, None, 20),
        snare(rate, seed=12, tone=230.0, snap=0.26),
        hat(rate, 0.025, level=0.7, seed=24),
        tom(rate, 140),
        tom(rate, 100),
        cymbal(rate, 2.0, 5500, 3200.0, 30),
        cowbell(rate),
        tambourine(rate),
        claves(rate),
    ]
