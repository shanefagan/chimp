from chimp import controllers
from chimp.desktop.config import Control, DesktopConfig, DeviceConfig, Profile
from chimp.desktop.learn import classify, decode
from chimp.desktop.templates import apply, clear_groups, controls_of, group_names, layout_for, pad_grid, split_banks

KO2 = controllers.find("EP-133")


def ep133(controls):
    return apply(KO2, controls)[1]


def ko2_learned() -> list[Control]:
    """What learning the KO II gives: 48 pads, notes 36-83 on channel 1 (groups A-D)."""
    ev, t = [], 0.0
    for n in range(36, 84):
        ev += [(t, [0x90, n, 100]), (t + 0.1, [0x80, n, 0])]
        t += 0.3
    return classify(ev)


def test_ko2_template_groups_and_names():
    controls = ko2_learned()
    assert len(controls) == 48
    assert layout_for("EP-133") is KO2 and layout_for("EP-133 #2") is KO2
    assert layout_for("Arturia MicroFreak") is None
    assert layout_for("MPD218") is None  # only its shape is known: Learn still teaches it
    assert ep133(controls)
    assert group_names(controls) == ["Group A", "Group B", "Group C", "Group D"]
    a = [c for c in controls if c.group == "Group A"]
    assert [c.name for c in a] == [f"A{i}" for i in range(1, 13)]
    assert [c.number for c in a] == list(range(36, 48))
    d12 = next(c for c in controls if c.number == 83)
    assert (d12.group, d12.name) == ("Group D", "D12")


def test_template_keeps_user_names():
    controls = ko2_learned()
    controls[0].name = "Mute mic"
    ep133(controls)
    assert controls[0].name == "Mute mic" and controls[0].group == "Group A"


def test_split_banks_and_clear():
    controls = ko2_learned()
    assert split_banks(controls, 16) == 3
    assert group_names(controls) == ["Bank 1", "Bank 2", "Bank 3"]
    clear_groups(controls)
    assert group_names(controls) == []


def test_other_group_for_ungrouped_controls():
    controls = ko2_learned() + classify([(0.0, [0xB0, 7, v]) for v in range(0, 128, 8)])
    ep133(controls)
    assert group_names(controls)[-1] == "Other"


def test_transport_buttons_learn_and_fire():
    controls = classify([(0.0, [0xFA]), (1.0, [0xFC]), (2.0, [0xF8])])  # start, stop, clock (ignored)
    assert [(c.kind, c.msg, c.number, c.describe()) for c in controls] == [
        ("button", "rt", 0xFA, "Start"),
        ("button", "rt", 0xFC, "Stop"),
    ]
    assert decode(controls[0], [0xFA]) == ("press", 1)
    ep133(controls)
    assert {c.group for c in controls} == {"Transport"}


def test_groups_survive_save_and_load(tmp_path):
    controls = ko2_learned()
    ep133(controls)
    cfg = DesktopConfig()
    cfg.devices["EP-133"] = DeviceConfig(True, [Profile("Default", controls)])
    cfg.save(tmp_path / "d.json")
    loaded = DesktopConfig.load(tmp_path / "d.json").devices["EP-133"].profiles[0].controls
    assert [(c.group, c.name) for c in loaded] == [(c.group, c.name) for c in controls]


def test_overlay_labels_and_columns():
    from chimp.desktop.config import Action
    from chimp.desktop.overlay import short_label

    assert pad_grid("EP-133", "Group B", 12) == (3, False)
    assert pad_grid("EP-133", "Transport", 3) == (0, False)  # not pads: a plain grid
    assert pad_grid("Arturia MicroFreak", "", 12) == (0, False)
    assert pad_grid("APC MINI", "Pads", 64) == (8, True)  # pad 1 is bottom-left
    assert short_label(Action("media", "playpause")) == "Play/Pause"
    assert short_label(Action("launch", "com.mitchellh.ghostty")) == "ghostty"
    assert short_label(Action("shortcut", "kwin/Overview")) == "Overview"
    assert short_label(Action("playing_volume", "2")) == "Playing #2"
    assert short_label(Action()) == ""


def test_a_known_controller_sets_up_without_learn():
    nk2 = layout_for("nanoKONTROL2")
    controls: list[Control] = []
    added, _ = apply(nk2, controls, add_missing=True)
    assert added == len(controls) == 51
    assert group_names(controls) == ["Knobs", "Faders", "Solo", "Mute", "Rec", "Transport"]
    fader3 = next(c for c in controls if c.name == "Fader 3")
    assert (fader3.kind, fader3.msg, fader3.channel, fader3.number) == ("fader", "cc", 1, 2)
    play = next(c for c in controls if c.name == "Play")
    assert (play.kind, play.mode, play.number) == ("button", "momentary", 41)
    assert apply(nk2, controls, add_missing=True) == (0, 0)  # nothing twice


def test_setting_up_keeps_what_you_did_and_adds_the_rest():
    from chimp.desktop.config import Action

    controls = ko2_learned()[:5]  # A1-A5 learned
    controls[0].name, controls[0].action = "Mute mic", Action("mic_mute")
    extra = classify([(0.0, [0xB0, 7, v]) for v in range(0, 128, 8)])  # something the KO II file doesn't have
    controls += extra
    added, _ = apply(KO2, controls, add_missing=True)
    assert added == 48 - 5 + 3  # the other pads, and the transport
    assert controls[0].name == "Mute mic" and controls[0].action.type == "mic_mute"
    assert [c.name for c in controls[:13]] == ["Mute mic"] + [f"A{i}" for i in range(2, 13)] + ["B1"]
    assert controls[-1] is extra[0] and group_names(controls)[-1] == "Other"


def test_a_device_on_another_channel_still_gets_its_layout():
    ev = []
    for n in (36, 37, 48):
        ev += [(n * 1.0, [0x94, n, 100]), (n + 0.1, [0x84, n, 0])]  # channel 5
    controls = classify(ev)
    apply(KO2, controls)
    assert [(c.name, c.group, c.channel) for c in controls] == [
        ("A1", "Group A", 5),
        ("A2", "Group A", 5),
        ("B1", "Group B", 5),
    ]


def test_every_shipped_controller_is_valid_and_distinct():
    found = controllers.load_all([(controllers.BUILTIN_DIR, False)])
    assert len(found) >= 12 and len(found) == len(list(controllers.BUILTIN_DIR.glob("*.yaml")))
    for c in found:
        made = controls_of(c)
        assert len({x.address for x in made}) == len(made), c.name
    assert controllers.find("APC mini mk2").id == "akai-apc-mini-mk2"
    assert controllers.find("APC MINI").id == "akai-apc-mini"
    assert controllers.find("LPD8 mk2").pads.size == 8 and controllers.find("LPD8").id == "akai-lpd8"
    assert controllers.find("Midi Through") is None


def test_your_own_controller_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "my-box.yaml").write_text(
        """
name: My box
match: My Box
pads: {columns: 2, rows: 2, channel: 3, banks: {A: [60, 62, 64, 65], B: 70}}
controls:
  - {kind: encoder, cc: 1-2, mode: rel2c}
  - {name: Bend, kind: fader, pb: true, channel: 2}
"""
    )
    (tmp_path / "akai-midimix.yaml").write_text("name: Mine\nmatch: [MIDI Mix]\ncontrols: [{kind: knob, cc: 5}]\n")
    for i, bad in enumerate(
        (
            "name: X\nmatch: [x]\n",  # nothing in it
            "name: X\nmatch: [x]\ncontrols: [{kind: knob, note: 1}]\n",
            "name: X\nmatch: [x]\ncontrols: [{kind: knob, cc: 1}, {kind: knob, cc: 1}]\n",
            "name: X\nmatch: [x]\npads: {columns: 4, rows: 2, banks: {A: [1, 2]}}\n",
            "name: X\nmatch: [x]\ncontrols: [{kind: button, cc: 200}]\n",
            "- nope\n",
            "name: [\n",
        )
    ):
        (tmp_path / f"bad{i}.yaml").write_text(bad)
    monkeypatch.setenv("CHIMP_CONTROLLERS_DIR", str(tmp_path))
    controllers.reset()
    try:
        box = controllers.find("my box:port 1")
        assert box.mine and box.pads.banks == (("A", (60, 62, 64, 65)), ("B", (70, 71, 72, 73)))
        assert [(s.name, s.mode, s.number) for s in box.controls[:2]] == [
            ("Encoder 1", "rel2c", 1),
            ("Encoder 2", "rel2c", 2),
        ]
        assert box.controls[2].msg == "pb"
        assert controllers.find("MIDI Mix").name == "Mine"  # yours replaces ours
        assert not any(c.name == "X" for c in controllers.known())
        assert capsys.readouterr().err.count("controllers: ") == 7
    finally:
        monkeypatch.delenv("CHIMP_CONTROLLERS_DIR")
        controllers.reset()


def test_names_count_from_start():
    from chimp import controllers

    c = controllers.parse(
        {"name": "X", "match": ["x"], "controls": [{"name": "Pad {n}", "start": 9, "kind": "button", "cc": "22-24"}]}
    )
    assert [s.name for s in c.controls] == ["Pad 9", "Pad 10", "Pad 11"]
    mini = controllers.find("X-TOUCH MINI MIDI 1")
    assert mini is not None and mini.name == "Behringer X-Touch Mini"
    assert len(mini.controls) == 66 and len(mini.knobs()) == 16
