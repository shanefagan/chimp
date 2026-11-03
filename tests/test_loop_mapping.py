"""Which MIDI message does what in the looper."""

from chimp.loop.mapping import HOLD_ACTIONS, LoopSettings, describe, key_of, release_of, value_of


def test_defaults_notes_play_sounds_and_lpd8_knobs_turn_params():
    s = LoopSettings()
    assert s.target("LPD8 mk2", "note:1:36") == "sound:0"
    assert s.target("LPD8 mk2", "note:10:43") == "sound:7"
    assert s.target("EP-133", "note:1:52") == "sound:0"  # wraps after 16
    assert s.target("LPD8 mk2", "cc:1:70") == "knob:0"
    assert s.target("LPD8 mk2", "cc:3:77") == "knob:7"
    assert s.target("LPD8 mk2", "cc:1:69") is None


def test_assigned_controls_win_and_move():
    s = LoopSettings()
    s.set_assign("KO II", "cc:1:21", "param:m.reverb")
    assert s.target("KO II", "cc:1:21") == "param:m.reverb"
    s.set_assign("KO II", "cc:1:22", "param:m.reverb")  # moves, not a second one
    assert s.target("KO II", "cc:1:21") is None and s.target("KO II", "cc:1:22") == "param:m.reverb"
    s.set_assign("LPD8 mk2", "cc:1:70", "action:record")  # a default knob CC reused as a button
    assert s.target("LPD8 mk2", "cc:1:70") == "record"
    assert s.params_assigned() == {"m.reverb": ["KO II · CC 22 · ch 1"]}


def test_message_parsing():
    assert key_of([0x90, 36, 100]) == ("note:1:36", 100 / 127)
    assert key_of([0x90, 36, 0]) is None  # note-off as velocity 0
    assert key_of([0xB1, 20, 127]) == ("cc:2:20", 1.0)
    assert key_of([0xB1, 20, 10]) is None  # a knob low down isn't a press
    assert value_of([0xB0, 70, 127]) == ("cc:1:70", 1.0)
    assert value_of([0xE2, 0x7F, 0x7F]) == ("pb:3:0", 1.0)
    assert value_of([0x90, 36, 100]) is None


def test_settings_round_trip(tmp_path):
    s = LoopSettings(bpm=96.0, fixed_velocity=True, sound={"swing": 0.4})
    s.set_assign("KO II", "note:1:60", "action:undo")
    path = tmp_path / "loop.json"
    s.save(path)
    t = LoopSettings.load(path)
    assert (t.bpm, t.fixed_velocity, t.sound, t.assign) == (
        96.0,
        True,
        {"swing": 0.4},
        {"KO II": {"note:1:60": "undo"}},
    )


def test_releases_and_hold_actions():
    assert release_of([0x80, 36, 0]) == "note:1:36"
    assert release_of([0x90, 36, 0]) == "note:1:36"
    assert release_of([0xB0, 20, 0]) == "cc:1:20"
    assert release_of([0x90, 36, 90]) is None
    assert {"roll", "select", "mute", "solo", "g.stutter16", "g.tapestop"} <= HOLD_ACTIONS
    s = LoopSettings()
    s.set_assign("Generic X-Box pad", "gpa:ABS_RZ", "param:m.drive")
    assert s.target("Generic X-Box pad", "gpa:ABS_RZ") == "param:m.drive"
    assert describe("gpa:ABS_RZ") == "Axis RZ" and describe("gpb:BTN_SOUTH") == "Button SOUTH"
