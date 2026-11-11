import json

from chimp import protocol as p
from chimp.desktop.config import KNOB_ACTIONS, PAD_ACTIONS, Action, DesktopConfig
from chimp.desktop.obs import _auth
from chimp.desktop.service import DesktopController


def test_defaults_put_actions_on_program_4_only():
    cfg = DesktopConfig()
    assert cfg.slots_with_actions() == [4]
    pa = cfg.actions(4)
    assert len(pa.knobs) == p.NUM_KNOBS and len(pa.pads) == p.NUM_PADS
    assert all(a.type in KNOB_ACTIONS for a in pa.knobs)
    assert all(a.type in PAD_ACTIONS for a in pa.pads)


def test_roundtrip(tmp_path):
    cfg = DesktopConfig(osd=False)
    cfg.actions(1).pads[3] = Action("command", "notify-send hi")
    path = tmp_path / "desktop.json"
    cfg.save(path)
    loaded = DesktopConfig.load(path)
    assert loaded == cfg
    assert loaded.slots_with_actions() == [1, 4]


def test_migrates_version_1(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "slot": 2,
                "pads": [{"type": "mic_mute", "target": ""}],
                "knobs": [{"type": "master_volume", "target": ""}],
            }
        )
    )
    cfg = DesktopConfig.load(path)
    assert cfg.slots_with_actions() == [2]
    assert cfg.actions(2).pads[0].type == "mic_mute"
    assert cfg.actions(2).knobs[0].type == "master_volume"
    assert cfg.actions(2).pads[1].type == "none"


def test_load_tolerates_junk(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"programs": {"1": {"pads": [{"type": "bo gus!"}, 7], "knobs": "nope"}, "x": {}}}))
    cfg = DesktopConfig.load(path)
    assert cfg.actions(1).pads[0].type == "none" and len(cfg.actions(1).pads) == 8
    assert len(cfg.actions(1).knobs) == 8
    path.write_text("{not json")
    assert DesktopConfig.load(path) == DesktopConfig()


def _factory(global_channel=1):
    prog = p.Program(global_channel=global_channel)
    for pad in prog.pads:
        pad.channel = 10
    return prog


def test_resolve_follows_active_program():
    ctl = DesktopController.__new__(DesktopController)  # no device / Qt objects needed
    ctl.programs = {1: _factory(), 2: _factory(), 4: p.Program(global_channel=16)}
    for pad in ctl.programs[4].pads:
        pad.channel = p.GLOBAL_CHANNEL
    ctl.active = 2
    switched = []
    ctl._on_active = lambda slot: (switched.append(slot), setattr(ctl, "active", slot))
    # identical mappings on P1/P2: use the polled active program
    assert ctl._resolve("note", 10, 36) == (2, 0)
    assert ctl._resolve("knob", 1, 71) == (2, 1)
    # channel 16 only exists on P4: that proves P4 is active, even before the next poll
    assert ctl._resolve("note", 16, 38) == (4, 2)
    assert switched == [4]
    assert ctl._resolve("note", 5, 36) is None


def test_obs_auth_matches_spec_example():
    # Worked example from the obs-websocket v5 protocol docs
    assert (
        _auth(
            "supersecretpassword",
            "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=",
            "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY=",
        )
        == "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4="
    )


def test_fullscreen_game_detection(tmp_path, monkeypatch):
    from chimp.desktop import system

    system._game_cache.clear()
    # window class alone is enough for Steam games and gamescope
    assert system.looks_like_game(10**8 + 1, "steam_app_1422450")
    assert system.looks_like_game(10**8 + 2, "gamescope")
    # a browser with nothing game-like about its process isn't one
    assert not system.looks_like_game(10**8 + 3, "brave-browser")
    assert not system.looks_like_game(0, "steam_app_1")


def test_midi_only_hint_shows_once_and_not_after_switch_popup(monkeypatch):
    from chimp.desktop import service

    shown = []
    monkeypatch.setattr(service.system, "osd_text", lambda icon, text: shown.append(text))
    ctl = DesktopController.__new__(DesktopController)
    ctl.cfg = DesktopConfig()  # actions on program 4 only
    ctl._informed = set()
    ctl.active = 1
    for _ in range(5):  # started on program 1, keeps playing pads
        ctl._maybe_hint()
    assert len(shown) == 1
    ctl._informed = {2}  # a switch to program 2 was announced
    ctl.active = 2
    ctl._maybe_hint()
    assert len(shown) == 1


def test_config_is_private_to_you(tmp_path):
    # it can hold the OBS websocket password
    path = tmp_path / "desktop.json"
    path.write_text("{}")
    path.chmod(0o644)  # a file from an older Chimp
    secret = "not-a-real-" + "password"
    cfg = DesktopConfig()
    cfg.obs.password = secret
    cfg.save(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert DesktopConfig.load(path).obs.password == secret
    assert not path.with_suffix(".json.tmp").exists()


def test_what_each_lpd8_program_is_for(tmp_path, monkeypatch):
    from chimp.loop import programs

    monkeypatch.setattr(programs, "CONFIG_DIR", tmp_path)  # no saved order of your own
    ctl = DesktopController.__new__(DesktopController)
    ctl.cfg = DesktopConfig()  # desktop actions on program 4
    ctl.looping = False
    assert ctl.program_use(4) == "desktop control" and ctl.program_use(1) == "MIDI only"
    ctl.looping = True  # Chimp Loop is open: the other programs are its pads
    assert ctl.program_use(1) == "Chimp Loop A: sounds 1–8"
    assert ctl.program_use(2) == "Chimp Loop B: sounds 9–16"
    assert ctl.program_use(3) == "Chimp Loop FX: roll and glitches"
    assert ctl.program_use(4) == "desktop control"
    (tmp_path / "loop.json").write_text('{"lpd8_jobs": ["fx", "A"]}')
    assert (
        ctl.program_use(1) == "Chimp Loop FX: roll and glitches"
        and ctl.program_use(3) == "Chimp Loop FX: roll and glitches"
    )
    assert ctl.program_use(2) == "Chimp Loop A: sounds 1–8"
