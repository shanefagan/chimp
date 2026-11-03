"""The looper engine, rendered offline with a kit of one-frame clicks so hits can be located exactly."""

import numpy as np
import pytest

from chimp.loop.engine import Engine
from chimp.loop.kit import NUM_SOUNDS, Kit, Sound, kit_names, load

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


def test_built_in_kit_is_synthesised():
    kit = load("synth", RATE)
    assert all(kit.sounds) and kit.labels()[:4] == ["Kick", "Snare", "Clap", "Hi-hat"]
    for s in kit.sounds:
        assert s.data.dtype == np.float32 and s.data.shape[1] == 2 and len(s.data) > RATE // 20
        assert np.isfinite(s.data).all() and 0.5 < np.abs(s.data).max() <= 0.9 + 1e-6
    kick = kit.sounds[0].data[:, 0]
    spec = np.abs(np.fft.rfft(kick)) ** 2
    f = np.fft.rfftfreq(len(kick), 1 / RATE)
    assert spec[f < 200].sum() / spec.sum() > 0.7  # a kick is low
    hat = kit.sounds[3].data[:, 0]
    spec = np.abs(np.fft.rfft(hat)) ** 2
    f = np.fft.rfftfreq(len(hat), 1 / RATE)
    assert spec[f > 5000].sum() / spec.sum() > 0.7  # a hat is high


def test_your_kits_load_from_the_data_folder(tmp_path, monkeypatch):
    import wave

    from chimp.loop import kit as kitmod

    folder = tmp_path / "heavy"
    folder.mkdir()
    for stem in ("kick", "snare_1", "zap"):
        with wave.open(str(folder / f"{stem}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes((np.sin(np.arange(4410) / 10) * 20000).astype("<i2").tobytes())
    monkeypatch.setattr(kitmod, "USER_DIR", tmp_path)
    assert kit_names() == ["synth", "heavy"]
    kit = load("heavy", RATE)
    assert kit.labels()[:3] == ["Kick", "Snare", "Zap"]  # named slots, then the rest in order
    assert len(kit.sounds[0].data) == 4800  # 44.1 kHz resampled to 48


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


def test_playing_a_pad_does_not_move_the_selection():
    e = engine()
    e.select(5)
    e.trigger(2)
    e.trigger(9)
    assert e.selected == 5


def test_pad_settings_act_on_the_selected_pad_and_render_in_the_background():
    e = engine()
    long = np.ones((RATE, 2), np.float32) * 0.1
    e.kit.sounds[2].data = long
    e.select(2)
    e.set_param("p.decay", 0.2)
    e.set_param("p.pitch", 0.75)  # +12 st: live, not baked into the sample
    assert e.pads[2]["pitch"] == 0.75 and e.pads[0]["pitch"] == 0.5
    assert e.wait_built()
    v = e._data(2)
    assert len(v) < RATE // 3 and v[-1, 0] == 0.0
    e.set_param("p.decay", 1.0)
    assert e.wait_built() and e._data(2) is long  # pitch alone needs no render


def test_pitch_bends_a_sound_that_is_already_ringing():
    e = engine()
    e.kit.sounds[0].data = np.ones((RATE, 2), np.float32) * 0.1  # a 1 s drone
    e.select(0)
    e.trigger(0)
    e.render(BLOCK)
    [v] = e._voices
    before = v.pos
    e.render(BLOCK)
    assert v.pos - before == BLOCK
    e.set_param("p.pitch", 0.75)  # +12 st while it plays
    before = v.pos
    e.render(BLOCK)
    assert v.pos - before == pytest.approx(2 * BLOCK)


def test_mute_and_solo_leave_parts_out_of_the_pattern_only():
    e = engine(bpm=120.0)
    for i in range(NUM_SOUNDS):
        e.kit.sounds[i].data = np.ones((RATE, 2), np.float32) * 0.01  # long enough to inspect
    for s in (0, 1, 2):
        e.toggle_step(s, 0)
    e.play_stop()

    def played():
        e.playhead = 0
        e._voices.clear()
        e.render(BLOCK)
        return sorted(v.slot for v in e._voices)

    assert played() == [0, 1, 2]
    e.toggle_mute(1)
    assert played() == [0, 2]
    e.toggle_solo(2)
    assert played() == [2]
    e.trigger(1)  # live playing still sounds, muted or not
    e._pending and e.render(BLOCK)
    assert any(v.slot == 1 for v in e._voices)


def test_roll_repeats_on_the_grid_and_records():
    e = engine(bars=1, bpm=120.0, quantise=0)
    e.roll_rate = 16
    run(e, 96000 + BLOCK, {0: e.record})
    e.record()  # overdub
    period = e.bar_frames() / 16
    start = e.abs
    e.roll_start(3, 0.9, e._block_time + (e.abs - e._block_abs) / RATE)
    run(e, round(period * 4) + BLOCK)
    e.roll_stop(3)
    run(e, round(period * 4))
    ts = sorted(h.t for h in e.hits() if h.sound == 3)
    assert 4 <= len(ts) <= 6  # the press, then one per sixteenth while held, then nothing
    gaps = np.diff(ts[1:])
    assert all(abs(g - period) <= 1 for g in gaps)
    assert e.abs > start


def glitch_run(e: Engine, mode: str, before: int, during: int) -> np.ndarray:
    out = [e.render(BLOCK) for _ in range(before // BLOCK)]
    e.glitch(mode)
    out += [e.render(BLOCK) for _ in range(during // BLOCK)]
    return np.concatenate(out)[:, 0]


def test_stutter_repeats_the_last_sixteenth():
    e = engine(bpm=120.0)
    e.kit.sounds[0].data = sine_kit = np.stack([np.linspace(0.1, 0.5, RATE, dtype=np.float32)] * 2, axis=1)
    del sine_kit
    e.trigger(0)
    y = glitch_run(e, "stutter16", RATE // 2, RATE)
    seg = round(e.bar_frames() / 16)
    tail = y[RATE // 2 + 2 * seg : RATE // 2 + 4 * seg]
    # a rising ramp, held to repeat: two consecutive repeats look the same
    assert np.allclose(tail[: seg - 200][100:], tail[seg : 2 * seg - 200][100:], atol=1e-3)


def test_tape_stop_winds_down_to_silence_and_release_is_live_again():
    e = engine()
    e.kit.sounds[0].data = np.ones((RATE * 3, 2), np.float32) * 0.2
    e.trigger(0)
    y = glitch_run(e, "tapestop", RATE // 4, RATE)
    assert np.abs(y[-BLOCK:]).max() < 1e-3  # stopped
    e.glitch_off()
    back = np.concatenate([e.render(BLOCK) for _ in range(8)])[:, 0]
    assert np.abs(back[-BLOCK:]).max() > 0.1


@pytest.mark.parametrize("mode", ["stutter4", "stutter8", "stutter32", "reverse", "half"])
def test_glitches_stay_finite_and_bounded(mode):
    e = engine()
    for i in range(8):
        e.trigger(i)
    y = glitch_run(e, mode, RATE // 3, RATE)
    assert np.isfinite(y).all() and np.abs(y).max() <= 1.0


def test_delay_time_change_glides_and_stays_sane():
    e = engine(bpm=120.0)
    e.set_param("m.delay", 0.8)
    e.set_param("m.echoes", 0.7)
    e.kit.sounds[0].data = np.ones((RATE, 2), np.float32) * 0.2
    e.trigger(0)
    for _ in range(40):
        e.render(BLOCK)
    e.set_param("m.delay_time", 0)  # long -> short while echoes ring: tape warble
    d0 = e.master.delay._d
    e.render(BLOCK)
    d1 = e.master.delay._d
    assert d1 < d0 and d1 > e.beat_frames() * 0.25  # moving, not jumped
    out = np.concatenate([e.render(BLOCK) for _ in range(100)])
    assert np.isfinite(out).all()


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
