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
    # (the master filter delays everything by 31 frames, so b starts with a's tail)
    assert a[:, 0].max() > 0 and b[:, 0].max() > 0.25 and c[:, 0].max() == 0


# --- pads, master, step sequencer ------------------------------------------------------


def impulse_response(e: Engine, frames: int, sound: int = 0) -> np.ndarray:
    e.trigger(sound)
    return np.concatenate([e.render(BLOCK) for _ in range(frames // BLOCK)])[:, 0]


def test_master_delay_echoes_a_dotted_eighth_later():
    e = engine(bpm=120.0)
    e.set_param("m.delay", 1.0)
    e.set_param("m.echoes", 0.0)
    e.set_param("m.pingpong", False)
    peaks = onsets(impulse_response(e, RATE))
    echo = round(RATE * 0.5 * 0.75)  # dotted eighth at 120 bpm
    assert len(peaks) == 2 and abs((peaks[1] - peaks[0]) - echo) <= 40


def test_master_filter_low_pass_darkens_and_high_pass_thins():
    e = engine()
    noise = np.random.default_rng(1).uniform(-0.5, 0.5, (RATE // 2, 2)).astype(np.float32)
    e.kit.sounds[0].data = noise

    def energy(v, lo, hi):
        e.set_param("m.filter", v)
        e._voices.clear()
        x = impulse_response(e, RATE // 2)[4000:]
        spec = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / RATE)
        return spec[(f >= lo) & (f < hi)].sum() / spec.sum()

    assert energy(0.5, 5000, 20000) > 0.4  # white noise: most energy is high
    assert energy(0.15, 5000, 20000) < 0.01  # low-pass well down
    assert energy(0.9, 0, 500) < 0.005  # high-pass up


def test_reverb_send_per_pad_and_return_overall():
    e = engine()
    e.set_param("m.reverb", 1.0)
    wet = np.abs(impulse_response(e, RATE)[RATE // 4 :]).max()
    e._voices.clear()
    for _ in range(RATE * 4 // BLOCK):  # let the tail die
        e.render(BLOCK)
    e.set_pad(0, "reverb", 0.0)  # this pad sends nothing
    dry = np.abs(impulse_response(e, RATE)[RATE // 4 :]).max()
    assert wet > 1e-3 and dry < wet / 100


def test_swing_pushes_every_second_sixteenth():
    e = engine(bars=1, bpm=120.0, quantise=16)
    e.length = round(e.bar_frames())
    g = e.grid_frames()
    e.set_param("m.swing", 1.0)
    assert e.placed(0) == 0
    assert e.placed(round(g)) == round(g + 0.5 * g)
    assert e.placed(round(2 * g)) == round(2 * g)


def test_pad_settings_act_on_the_selected_pad_and_render_in_the_background():
    e = engine()
    long = np.ones((RATE, 2), np.float32) * 0.1
    e.kit.sounds[2].data = long
    e.trigger(2)
    assert e.selected == 2
    e.set_param("p.pitch", 0.75)  # +12 semitones: half as long
    e.set_param("p.decay", 0.2)
    assert e.pads[2]["pitch"] == 0.75 and e.pads[0]["pitch"] == 0.5
    assert e.wait_built()
    v = e._data(2)
    assert len(v) < RATE // 2 and v[-1, 0] == 0.0
    e.set_param("p.decay", 1.0)
    e.set_param("p.pitch", 0.5)
    assert e.wait_built() and e._data(2) is long  # back to the sample as it is


def test_pad_level_and_pan_act_at_once():
    e = engine()
    e.set_pad(0, "pan", 0.0)  # hard left
    e.trigger(0)
    out = e.render(BLOCK)
    assert np.abs(out[:, 0]).max() > 0.3 and np.abs(out[:, 1]).max() < 1e-6


def test_pad_filter_type_switch_starts_open():
    e = engine()
    e.set_pad(0, "filter_type", "hp")
    assert e.pads[0]["cutoff"] == 0.0
    e.set_pad(0, "filter_type", "bp")
    assert e.pads[0]["cutoff"] == 0.5


def test_presets_set_the_master_and_keep_volume():
    from chimp.loop.engine import PRESETS

    e = engine()
    e.set_param("m.volume", 0.6)
    e.apply_preset("Abyss")
    assert e.master.reverb.mode == "sink" and e.master_settings["reverb"] == PRESETS["Abyss"]["reverb"]
    assert e.volume == 0.6
    e.apply_preset("Clean")
    assert e.master.reverb.mix == 0.0 and e.master.drive == 0.0


def test_step_entry_makes_a_bar_and_toggles():
    e = engine(bpm=120.0)
    e.toggle_step(0, 0)
    e.toggle_step(1, 4)
    e.toggle_step(0, 8)
    assert e.state == "stopped" and e.length == round(e.bar_frames()) and e.steps() == 16
    g = e.grid(0)
    assert g[0][0] == 0.8 and g[1][4] == 0.8 and g[0][8] == 0.8 and sum(map(sum, g)) == pytest.approx(2.4)
    e.toggle_step(0, 8)  # off again
    assert e.grid(0)[0][8] == 0
    e.set_step_velocity(1, 4, 1.0)
    assert e.grid(0)[1][4] == 1.0
    e.undo()  # takes back the last step entered (sound 1 on step 4)
    assert e.grid(0)[1][4] == 0


def test_recorded_hits_show_on_the_grid():
    e = engine(bars=1, bpm=120.0, quantise=0)
    sf = round(e.step_frames())
    run(e, 96000 + BLOCK * 2, {0: e.record, sf * 3 + 200: lambda now: e.trigger(5, 0.5, now)})
    assert e.grid(0)[5][3] == pytest.approx(0.5, abs=0.01)


def test_sound_settings_round_trip_through_save():
    from chimp.loop import files

    e = engine(bars=1)
    e.length = round(e.bar_frames())
    e.layers = [[]]
    e.selected = 5
    e.set_param("p.pitch", 0.25)
    e.set_param("p.filter_type", "hp")
    e.set_param("m.reverb", 0.3)
    e.set_param("m.reverb_mode", "rise")
    d = files.to_dict(e, "clicks")
    f = engine()
    files.from_dict(f, d)
    assert f.pads[5]["pitch"] == 0.25 and f.pads[5]["filter_type"] == "hp"
    assert f.master.reverb.mix == 0.3 and f.master.reverb.mode == "rise"


def test_everything_on_fits_in_a_block():
    import time

    e = engine()
    e.apply_preset("Abyss")
    e.set_param("m.filter", 0.2)
    for i in range(16):
        e.set_pad(i, "drive", 0.3)
        e.set_pad(i, "cutoff", 0.6)
        e.set_pad(i, "resonance", 0.5)
    assert e.wait_built()
    for i in range(16):
        e.trigger(i)
    for _ in range(20):
        e.render(BLOCK)
    t = time.perf_counter()
    for _ in range(200):
        e.render(BLOCK)
    per_block = (time.perf_counter() - t) / 200
    assert per_block < (BLOCK / RATE) * 0.35, f"{per_block * 1000:.2f} ms per {BLOCK / RATE * 1000:.1f} ms block"
