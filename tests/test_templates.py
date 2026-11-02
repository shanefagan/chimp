from chimp.desktop.config import Control, DesktopConfig, DeviceConfig, Profile
from chimp.desktop.learn import classify, decode
from chimp.desktop.templates import clear_groups, ep133, group_names, split_banks, template_for


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
    assert template_for("EP-133")[1] is ep133
    assert template_for("EP-133 #2") is not None and template_for("LPD8 mk2") is None
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
    from chimp.desktop.templates import overlay_columns

    assert overlay_columns("EP-133") == 3 and overlay_columns("Arturia MicroFreak") == 0
    assert short_label(Action("media", "playpause")) == "Play/Pause"
    assert short_label(Action("launch", "com.mitchellh.ghostty")) == "ghostty"
    assert short_label(Action("shortcut", "kwin/Overview")) == "Overview"
    assert short_label(Action("playing_volume", "2")) == "Playing #2"
    assert short_label(Action()) == ""
