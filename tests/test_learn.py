from chimp.desktop.config import Control
from chimp.desktop.learn import classify, decode


def cc(ch, num, val):
    return [0xB0 | (ch - 1), num, val]


def note(ch, n, vel):
    return [0x90 | (ch - 1), n, vel]


def timeline(msgs, dt=0.02, start=0.0):
    return [(start + i * dt, m) for i, m in enumerate(msgs)]


def test_knob_sweep_is_absolute_knob():
    ev = timeline([cc(1, 70, v) for v in list(range(0, 128, 3)) + list(range(127, 0, -5))])
    [c] = classify(ev)
    assert (c.kind, c.mode, c.msg, c.channel, c.number) == ("knob", "absolute", "cc", 1, 70)


def test_small_slow_knob_move_near_middle_is_still_a_knob():
    ev = timeline([cc(1, 21, v) for v in (60, 61, 62, 63, 64, 65, 66)], dt=0.1)
    assert classify(ev)[0].kind == "knob"


def test_rel64_encoder():
    ev = timeline([cc(2, 10, v) for v in [65] * 6 + [66, 65, 63, 63, 62, 63]])
    [c] = classify(ev)
    assert (c.kind, c.mode) == ("encoder", "rel64")
    assert decode(c, cc(2, 10, 66)) == ("step", 2)
    assert decode(c, cc(2, 10, 63)) == ("step", -1)


def test_twos_complement_encoder():
    ev = timeline([cc(1, 16, v) for v in [1, 1, 1, 2, 1, 127, 127, 126, 127]])
    [c] = classify(ev)
    assert (c.kind, c.mode) == ("encoder", "rel2c")
    assert decode(c, cc(1, 16, 127)) == ("step", -1)
    assert decode(c, cc(1, 16, 3)) == ("step", 3)


def test_cc_buttons_momentary_and_toggle():
    momentary = [(0.0, cc(1, 20, 127)), (0.15, cc(1, 20, 0)), (1.0, cc(1, 20, 127)), (1.2, cc(1, 20, 0))]
    toggle = [(0.0, cc(1, 21, 127)), (2.0, cc(1, 21, 0)), (4.0, cc(1, 21, 127)), (6.0, cc(1, 21, 0))]
    a, b = classify(momentary + [(t + 10, m) for t, m in toggle])
    assert (a.kind, a.mode) == ("button", "momentary")
    assert (b.kind, b.mode) == ("button", "toggle")
    assert decode(a, cc(1, 20, 127)) == ("press", 1)
    assert decode(a, cc(1, 20, 0)) == ("release", 0)
    assert decode(b, cc(1, 21, 0)) == ("press", 1)


def test_notes_pitchbend_pc_and_ordering():
    ev = [
        (0.0, note(10, 36, 90)),
        (0.1, [0x80 | 9, 36, 0]),
        (0.5, [0xE0, 0, 64]),
        (0.6, [0xE0, 0x7F, 0x7F]),
        (0.9, [0xC0, 5]),
        (1.0, [0xD0, 40]),  # channel pressure: ignored
    ]
    names = [(c.name, c.kind, c.msg) for c in classify(ev)]
    assert names == [("Button 1", "button", "note"), ("Fader 1", "fader", "pb"), ("Button 2", "button", "pc")]
    fader = Control("f", "fader", "pb", 1)
    assert decode(fader, [0xE0, 0x7F, 0x7F]) == ("level", 1.0)


def test_14bit_lsb_partner_dropped():
    ev = timeline([m for v in range(0, 128, 8) for m in (cc(1, 7, v), cc(1, 39, v))])
    controls = classify(ev)
    assert [(c.msg, c.number) for c in controls] == [("cc", 7)]


def test_lpd8_factory_layout_learns_as_8_knobs_and_8_pads():
    # What the LPD8 sends on factory Program 1: knobs CC 70-77 ch 1, pads notes 36-43 ch 10
    ev, t = [], 0.0
    for k in range(8):
        for v in range(0, 128, 9):
            ev.append((t, cc(1, 70 + k, v)))
            t += 0.01
    for p in range(8):
        for _ in range(2):
            ev += [(t, note(10, 36 + p, 100)), (t + 0.1, [0x80 | 9, 36 + p, 0])]
            t += 0.5
    controls = classify(ev)
    assert [c.name for c in controls] == [f"Knob {i}" for i in range(1, 9)] + [f"Button {i}" for i in range(1, 9)]
    assert all(c.mode == "momentary" for c in controls[8:])


def test_14bit_knob_nudged_near_centre_is_a_knob_not_an_encoder():
    # High byte flips between 64 and 65 (repeating, all near 64: encoder-shaped on its own)
    # while the LSB sweeps; the LSB partner proves it's an absolute 14-bit control.
    msgs = []
    for i in range(20):
        msgs += [cc(1, 1, 64 if i < 6 else 65), cc(1, 33, (i * 6) % 128)]
    [c] = classify(timeline(msgs))
    assert (c.kind, c.mode, c.number) == ("knob", "absolute", 1)
