"""The soundboard: "Play sound" pads, their modes, and the mix (no audio device needed)."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from chimp.desktop import actions, soundboard
from chimp.desktop.config import Action, DesktopConfig
from chimp.desktop.soundboard import FADE, Mixer, Voice, settings
from chimp.loop.kit import write_wav


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def no_audio(monkeypatch):
    """Outputs that never open a real device."""
    opened = []
    monkeypatch.setattr(soundboard.Output, "ensure_open", lambda self: opened.append(self.device))
    return opened


def clip(tmp_path, name="boom.wav", frames=4800, level=0.5):
    path = tmp_path / name
    write_wav(path, np.full((frames, 2), level, np.float32), soundboard.RATE)
    return str(path)


def test_settings_are_checked():
    assert settings(None) == soundboard.Settings("oneshot", "", 100)
    s = settings({"mode": "hold", "output": " Headphones ", "volume": 50})
    assert s == soundboard.Settings("hold", "Headphones", 50) and s.gain == pytest.approx(0.25)
    assert settings({"mode": "loop", "volume": "loud", "output": 7}) == soundboard.Settings()
    assert settings({"volume": 250}).volume == 100 and settings({"volume": True}).volume == 100


def test_voices_overlap_and_end():
    m = Mixer()
    data = np.full((100, 2), 0.25, np.float32)
    m.add(Voice("a", data, 1.0))
    m.add(Voice("a", data, 1.0))
    out = m.render(150)
    assert out[0, 0] == pytest.approx(0.5) and out[99, 0] == pytest.approx(0.5) and out[100, 0] == 0
    assert m.voices == [] and m.playing() == set()


def test_a_stop_fades_out_without_a_click():
    m = Mixer()
    m.add(Voice("a", np.ones((10_000, 2), np.float32), 1.0))
    m.render(10)
    assert m.stop("b") == 0 and m.stop("a") == 1 and m.playing() == set()
    tail = m.render(FADE + 50)[:, 0]
    assert tail[0] == pytest.approx(1.0) and np.all(np.diff(tail[:FADE]) <= 0) and np.all(tail[FADE:] == 0)
    assert m.voices == []


def test_the_mix_is_clipped():
    m = Mixer()
    for _ in range(4):
        m.add(Voice("x", np.full((10, 2), 0.6, np.float32), 1.0))
    assert m.render(10).max() == pytest.approx(1.0)


def test_modes(tmp_path, no_audio):
    path = clip(tmp_path)
    sb = soundboard.Soundboard(report=lambda _t: None)
    mixer = lambda: sb._outputs[""].mixer  # noqa: E731

    sb.press("pad1", path, {"mode": "oneshot"})
    sb.press("pad1", path, {"mode": "oneshot"})
    assert len(mixer().voices) == 2 and set(no_audio) == {""}

    sb.stop_all()
    mixer().render(FADE)
    sb.press("pad2", path, {"mode": "toggle"})
    assert mixer().playing() == {"pad2"}
    sb.press("pad2", path, {"mode": "toggle"})  # again: stops
    assert mixer().playing() == set()
    mixer().render(FADE)

    sb.press("pad3", path, {"mode": "restart"})
    mixer().render(1000)
    sb.press("pad3", path, {"mode": "restart"})
    fresh = [v for v in mixer().voices if v.fade < 0]
    assert len(fresh) == 1 and fresh[0].pos == 0  # the old one fades, the new one starts at the top
    sb.stop_all()
    mixer().render(FADE)

    sb.press("pad4", path, {"mode": "hold", "volume": 50})
    assert mixer().playing() == {"pad4"} and mixer().voices[0].gain == pytest.approx(0.25)
    sb.release("pad4", {"mode": "oneshot"})  # not a hold: the release does nothing
    assert mixer().playing() == {"pad4"}
    sb.release("pad4", {"mode": "hold"})
    assert mixer().playing() == set()
    sb.shutdown()


def test_each_output_has_its_own_stream(tmp_path, no_audio):
    path = clip(tmp_path)
    sb = soundboard.Soundboard(report=lambda _t: None)
    sb.press("a", path, {"output": "Headphones"})
    sb.press("b", path, {})
    assert sorted(no_audio) == ["", "Headphones"]
    assert sb.playing() == {"a", "b"}
    sb.shutdown()


def test_a_missing_file_is_reported(tmp_path, no_audio):
    said = []
    sb = soundboard.Soundboard(report=said.append)
    sb.press("a", str(tmp_path / "nope.wav"), {})
    sb.press("b", "", {})
    assert said == ["Play sound: nope.wav: file not found", "Play sound: no file chosen"]
    assert no_audio == []
    sb.shutdown()


def test_a_changed_file_is_read_again(tmp_path):
    path = clip(tmp_path, level=0.5)
    clips = soundboard.Clips()
    clips.load(soundboard.clip_path(path))
    assert clips.get(soundboard.clip_path(path))[0, 0] == pytest.approx(0.5, abs=1e-3)
    write_wav(soundboard.clip_path(path), np.full((10, 2), 0.25, np.float32), soundboard.RATE)
    assert clips.get(soundboard.clip_path(path)) is None  # stale
    clips.load(soundboard.clip_path(path))
    assert clips.get(soundboard.clip_path(path))[0, 0] == pytest.approx(0.25, abs=1e-3)
    clips.keep_only(set())
    assert clips.get(soundboard.clip_path(path)) is None


def test_options_are_saved_with_the_pad(tmp_path):
    cfg = DesktopConfig()
    cfg.actions(3).pads[1] = Action("sound", "~/clips/airhorn.mp3", {"mode": "hold", "volume": 70, "output": "HDMI"})
    path = tmp_path / "desktop.json"
    cfg.save(path)
    again = DesktopConfig.load(path)
    assert again.actions(3).pads[1] == cfg.actions(3).pads[1]
    assert again.sound_targets() == ["~/clips/airhorn.mp3"]
    assert Action("mic_mute").to_dict() == {"type": "mic_mute", "target": ""}  # no options: none saved
    junk = Action.from_dict(
        {"type": "sound", "target": "x", "options": {"mode": "hold", "bad": [1, 2], "big": "y" * 600}}
    )
    assert junk.options == {"mode": "hold"}


def test_the_runner_plays_and_releases_by_pad(tmp_path, monkeypatch):
    calls = []

    class Fake:
        def press(self, key, target, options):
            calls.append(("press", key, options.get("mode")))

        def release(self, key, options):
            calls.append(("release", key, options.get("mode")))

        def stop_all(self):
            calls.append(("stop",))

    runner = actions.ActionRunner(None, osd=lambda: False, report=lambda _t: None, audio_changed=lambda: None)
    runner._sounds = Fake()
    a = Action("sound", " /x/boom.wav ", {"mode": "hold"})
    runner.press(a, "Pad 2")
    runner.release(a, "Pad 2")
    runner.press(Action("sound_stop"), "Pad 8")
    assert calls == [("press", "Pad 2|/x/boom.wav", "hold"), ("release", "Pad 2|/x/boom.wav", "hold"), ("stop",)]


def test_the_editor_sets_a_pads_sound_options(monkeypatch, tmp_path):
    from chimp.desktop import bridge as bridge_mod

    b = bridge_mod.DesktopBridge.__new__(bridge_mod.DesktopBridge)
    b.cfg = DesktopConfig()
    b._revision = 0
    monkeypatch.setattr(b, "_changed", lambda bump=True, device=False: None, raising=False)
    b.cfg.actions(2).pads[0] = Action("sound", "/x/a.wav")
    b.setOption(2, True, 0, "mode", "toggle")
    b.setOption(2, True, 0, "volume", "62.6")
    assert b.cfg.actions(2).pads[0].options == {"mode": "toggle", "volume": 63}
    assert b.action(2, True, 0)["options"] == {"mode": "toggle", "volume": 63}
    types = {t["id"]: t for t in bridge_mod._types(bridge_mod.PAD_ACTIONS)}
    assert types["sound"]["file"] and types["sound"]["sound"] and not types["media"]["file"]
