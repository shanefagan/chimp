"""The looper's DSP: filters, drive, dynamics, shifter, delay and reverb."""

import time

import numpy as np
import pytest

from chimp.loop import dsp
from chimp.loop.fx import Delay, Master, Reverb

RATE = 48000
B = dsp.BLOCK


def sine(freq: float, secs: float = 0.5, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(RATE * secs)) / RATE
    m = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([m, m], axis=1)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def blocks(fn, x: np.ndarray) -> np.ndarray:
    return np.concatenate([fn(x[i : i + B]) for i in range(0, len(x), B)])


def peak_freq(x: np.ndarray) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / RATE)[np.argmax(spec)])


def test_offline_filter_cuts_and_resonates():
    lo = dsp.filter_offline(sine(4000), "lp", 1000, 0.707, RATE)
    assert rms(lo) < rms(sine(4000)) * 0.05  # 24 dB/oct: two octaves up is way down
    hi = dsp.filter_offline(sine(100), "hp", 1000, 0.707, RATE)
    assert rms(hi) < rms(sine(100)) * 0.01
    peak = dsp.filter_offline(sine(1000), "lp", 1000, 8.0, RATE)
    assert rms(peak[RATE // 10 : RATE // 2]) > rms(sine(1000)) * 4  # resonance boosts at the cutoff


def test_live_filter_sweeps_without_blowing_up():
    f = dsp.FftFilter(RATE)
    x = sine(5000, 1.0)
    out = []
    for i in range(0, len(x), B):
        v = 0.2 if i > len(x) // 4 else 0.5  # off, then a low-pass sweep down
        out.append(f.process(x[i : i + B], dsp.dj_setting(v, 0.6)))
    y = np.concatenate(out)
    assert np.isfinite(y).all()
    assert rms(y[: len(x) // 5]) == pytest.approx(rms(x[: len(x) // 5]), rel=0.01)  # off = untouched
    assert rms(y[-RATE // 4 :]) < rms(x) * 0.01


def test_drive_kinds_stay_bounded():
    x = sine(110, 0.2, 0.9)
    for kind in dsp.DRIVE_TYPES:
        y = dsp.drive(x, kind, 1.0)
        assert np.isfinite(y).all() and np.abs(y).max() <= 1.0, kind
    crushed = dsp.drive(x, "crush", 1.0)
    assert len(np.unique(crushed)) < 20  # 3 bits
    assert dsp.drive(x, "soft", 0.0) is x


def test_envelope_shapes():
    assert dsp.envelope(1000, RATE, 0.0, 1.0, 1.0) is None
    env = dsp.envelope(RATE, RATE, 0.5, 0.2, 0.2)  # 125 ms attack, short hold, fast decay
    peak = int(np.argmax(env))
    assert 0.11 * RATE < peak < 0.2 * RATE and env[0] == 0.0 and env[-1] < 1e-3


def test_compressor_reduces_loud_more_than_quiet():
    c = dsp.Compressor(RATE)
    c.amount = 0.8
    loud = blocks(c.run, sine(200, 1.0, 0.9))
    c2 = dsp.Compressor(RATE)
    c2.amount = 0.8
    quiet = blocks(c2.run, sine(200, 1.0, 0.05))
    gain_loud = rms(loud[-RATE // 4 :]) / rms(sine(200, 0.25, 0.9))
    gain_quiet = rms(quiet[-RATE // 4 :]) / rms(sine(200, 0.25, 0.05))
    assert gain_loud < gain_quiet / 3


def test_limiter_never_passes_the_ceiling():
    lim = dsp.Limiter(RATE)
    lim.drive = 1.0
    x = (np.random.default_rng(3).standard_normal((RATE, 2)) * 0.8).astype(np.float32)
    y = blocks(lim.run, x)
    assert np.abs(y).max() <= lim.ceiling + 1e-6
    assert rms(y) > 0.25  # loud (peaks were ~20x over the ceiling), not crushed to nothing


@pytest.mark.parametrize(("ratio", "want"), [(2.0, 880.0), (0.5, 220.0)])
def test_shifter_moves_an_octave(ratio, want):
    s = dsp.Shifter(RATE, 0.05)
    x = sine(440, 1.0)[:, 0]
    y = np.concatenate([s.run(x[i : i + B], ratio) for i in range(0, len(x), B)])
    assert peak_freq(y[RATE // 4 :]) == pytest.approx(want, rel=0.03)


def test_pingpong_delay_starts_on_the_left():
    d = Delay(RATE)
    d.mix, d.feedback = 1.0, 0.5
    x = np.zeros((RATE * 2, 2), np.float32)
    x[0] = 1.0
    beat = RATE / 2  # 120 bpm
    y = np.concatenate([d.run(x[i : i + B], beat) for i in range(0, len(x), B)])
    first = int(np.argmax(np.abs(y[:, 0]) > 0.1))
    assert abs(first - beat * 0.75) < 50
    assert np.abs(y[first - 5 : first + 50, 1]).max() < 0.05  # right silent at the first repeat
    second = int(np.argmax(np.abs(y[:, 1]) > 0.05))
    assert abs(second - 2 * beat * 0.75) < 100


@pytest.mark.parametrize("mode", ["hall", "rise", "sink", "reverse"])
def test_reverb_modes_ring_and_stay_stable(mode):
    r = Reverb(RATE)
    r.mix, r.size, r.shimmer, r.mode = 1.0, 1.0, 1.0, mode  # the most it can feed back
    x = np.zeros((RATE * 12, 2), np.float32)
    x[:2000] = sine(220, 2000 / RATE)
    y = np.concatenate([r.run(x[i : i + B]) for i in range(0, len(x), B)])
    assert np.isfinite(y).all()
    assert rms(y[RATE // 2 : RATE]) > 1e-3  # a tail
    assert np.abs(y[-RATE:]).max() < 2.0  # no runaway


def test_reverb_size_sets_decay_time():
    def tail(size):
        r = Reverb(RATE)
        r.mix, r.size = 1.0, size
        x = np.zeros((RATE * 3, 2), np.float32)
        x[0] = 1.0
        y = np.concatenate([r.run(x[i : i + B]) for i in range(0, len(x), B)])
        return rms(y[2 * RATE :]) / rms(y[RATE // 10 : RATE // 2])

    assert tail(0.2) < tail(0.8) / 10


def test_master_bus_everything_on_fits_in_a_block():
    m = Master(RATE)
    m.delay.mix, m.reverb.mix, m.reverb.mode, m.reverb.shimmer = 0.5, 0.6, "sink", 0.7
    m.drive, m.drive_type, m.filter_value, m.resonance, m.comp.amount = 0.4, "fuzz", 0.25, 0.7, 0.6
    m.limiter.drive = 0.5
    x = (np.random.default_rng(5).standard_normal((B, 2)) * 0.3).astype(np.float32)
    for _ in range(20):
        m.process(x, x, x, RATE / 2)
    t = time.perf_counter()
    for _ in range(200):
        y = m.process(x, x, x, RATE / 2)
    per = (time.perf_counter() - t) / 200
    assert np.abs(y).max() <= m.limiter.ceiling * m.volume + 1e-6
    assert per < (B / RATE) * 0.3, f"{per * 1000:.2f} ms per {B / RATE * 1000:.2f} ms block"
