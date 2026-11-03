"""The looper engine, rendered offline with a kit of one-frame clicks so hits can be located exactly."""

import numpy as np
import pytest

from chimp.loop.engine import Engine
from chimp.loop.kit import NUM_SOUNDS, Kit, Sound, kit_dirs, load_kit

RATE = 48000
BLOCK = 256


def click_kit() -> Kit:
    kit = Kit("clicks", RATE)
    for i in range(NUM_SOUNDS):
        data = np.zeros((32, 2), np.float32)
        data[0] = 0.5 + i / 100  # each sound has its own height, to tell them apart
        kit.sounds[i] = Sound(f"s{i}", 36 + i, data)
    return kit


def engine(**kw) -> Engine:
    e = Engine(RATE)
    e.set_kit(click_kit())
    e.volume = 1.0
    e.metronome = False
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def run(e: Engine, frames: int, actions: dict[int, callable] | None = None) -> np.ndarray:
    """Render `frames`, calling actions[frame](now) at that absolute frame (heard time)."""
    actions = dict(actions or {})
    out = []
    done = 0
    while done < frames:
        block_start = e.abs
        for at in sorted(a for a in actions if block_start - BLOCK <= a < block_start):
            # an event during the previous block's playback: heard at frame `at`
            fn = actions.pop(at)
            fn(e._block_time + (at - e._block_abs + e.latency) / RATE)
        out.append(e.render(BLOCK))
        done += BLOCK
    return np.concatenate(out)[:, 0]


def onsets(signal: np.ndarray) -> list[int]:
    return [int(i) for i in np.flatnonzero(signal > 0.25)]


def test_bundled_kits_load_16_named_sounds():
    names = [d.name for d in kit_dirs()]
    assert {"drums1", "drums2"} <= set(names)
    kit = load_kit(next(d for d in kit_dirs() if d.name == "drums1"), RATE)
    assert all(kit.sounds)
    assert kit.labels()[:4] == ["Kick", "Snare", "Clap", "Hi-hat"]
    assert kit.sounds[0].data.dtype == np.float32 and kit.sounds[0].data.shape[1] == 2


def test_free_take_sets_length_and_plays_hits_where_they_were_heard():
    e = engine(quantise=0)
    start, hit1, hit2, end = 1000, 1000 + 4800, 1000 + 24000, 1000 + 48000
    out = run(
        e,
        48000 * 3,
        {
            start: e.record,
            hit1: lambda now: e.trigger(0, 1.0, now),
            hit2: lambda now: e.trigger(1, 1.0, now),
            end: e.record,
        },
    )
    assert e.state == "playing"
    assert abs(e.length - 48000) <= 1
    assert e.bpm == pytest.approx(
        240.0 / 2, rel=0.01
    )  # 1 s = 2 beats at 120? no: 4 beats -> 240 bpm out of range; 2 beats -> 120
    # Second pass: hits repeat one loop length after they were played.
    later = [o for o in onsets(out) if o > end]
    assert any(abs(o - (hit1 + e.length)) <= BLOCK for o in later)
    assert any(abs(o - (hit2 + e.length)) <= BLOCK for o in later)


def test_quantise_snaps_on_playback_and_can_be_turned_off():
    e = engine(bars=1, bpm=120.0, quantise=16)  # 1 bar = 96000 frames, 1/16 = 6000
    rec, late_hit = 0, 6000 + 900  # 900 frames late for the second sixteenth
    run(e, 96000 + BLOCK * 2, {rec: e.record, rec + late_hit: lambda now: e.trigger(2, 1.0, now)})
    assert e.state == "playing" and e.length == 96000
    [h] = e.hits()
    assert abs(h.t - late_hit) <= 2
    assert e.placed(h.t) == 6000
    e.quantise = 0
    assert e.placed(h.t) == h.t


def test_overdub_hit_quantised_forward_is_not_played_twice():
    e = engine(bars=1, bpm=120.0, quantise=4)  # quarter notes: grid 24000
    run(e, 96000 + BLOCK, {0: e.record})
    e.record()  # playing -> overdub
    assert e.state == "overdub"
    base = e.abs
    # Hit 2000 frames early for beat 2: heard live now, would also be scheduled on the grid.
    target = (24000 - (e.playhead % 96000)) % 96000 + base - 2000
    out = run(e, 30000, {target: lambda now: e.trigger(5, 1.0, now)})
    hits_heard = onsets(out)
    assert len(hits_heard) == 1


def test_undo_and_clear():
    e = engine(bars=1, quantise=0)
    run(e, BLOCK * 4, {0: e.record, 300: lambda now: e.trigger(0, 1.0, now)})
    run(e, 96000)
    e.record()
    run(e, BLOCK * 4, {e.abs + 100: lambda now: e.trigger(1, 1.0, now)})
    assert len(e.layers) == 2
    e.undo()
    assert len(e.layers) == 2 and e.layers[-1] == []  # still overdubbing: a fresh layer
    e.record()  # overdub off
    assert len(e.layers) == 1
    e.clear()
    assert e.state == "empty" and e.length == 0 and e.hits() == []


def test_open_hat_is_choked_by_closed_hat():
    e = engine()
    long = np.ones((RATE, 2), np.float32) * 0.1
    e.kit.sounds[4].data = long
    e.trigger(4)
    a = e.render(BLOCK)
    e.trigger(3)
    b = e.render(BLOCK)
    c = e.render(BLOCK)
    assert a[:, 0].max() > 0 and b[0, 0] > 0.5 and c[:, 0].max() == 0
