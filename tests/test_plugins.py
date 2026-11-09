"""Plugins: your own pad actions from ~/.local/share/chimp/plugins/, read lazily."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from chimp.desktop import actions, config, overlay, plugins
from chimp.desktop.config import Action, DesktopConfig

LIGHTS = '''
"""A stand-in plugin."""
import json, os

LOG = os.environ["PLUGIN_LOG"]
open(LOG, "a").write("imported\\n")

ACTIONS = {
    "lights_hold": {"label": "Desk lights: on while held", "short": "Lights (hold)",
                      "hold": True, "colour": [0, 160, 255]},
    "lights_toggle": {"label": "Desk lights: on/off", "field": "Speed", "hint": "1-10"},
    "mic_mute": {"label": "trying to take a built-in"},
    "bad id": {"label": "not an identifier"},
    "no_label": {},
}

def _log(*a):
    open(LOG, "a").write(json.dumps(a) + "\\n")

def press(action, target):
    _log("press", action, target)

def release(action, target):
    _log("release", action, target)

def marker_note():
    return "§4 …and that is why"
'''


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """A plugins folder, with Chimp's action tables put back afterwards."""
    folder = tmp_path / "plugins"
    folder.mkdir()
    log = tmp_path / "log"
    log.write_text("")
    monkeypatch.setenv("CHIMP_PLUGINS_DIR", str(folder))
    monkeypatch.setenv("PLUGIN_LOG", str(log))
    saved = (dict(config.PAD_ACTIONS), dict(config.BUTTON_ACTIONS), set(config.HOLD_ACTIONS), dict(config.ON_COLOURS))
    plugins.reset()
    yield folder
    plugins.reset()
    for table, old in zip(
        (config.PAD_ACTIONS, config.BUTTON_ACTIONS, config.HOLD_ACTIONS, config.ON_COLOURS), saved, strict=True
    ):
        table.clear()
        table.update(old)
    for name in [m for m in sys.modules if m.startswith("chimp_plugin_")]:
        del sys.modules[name]


def log_lines(folder):
    return (folder.parent / "log").read_text().splitlines()


def test_actions_are_listed_without_running_the_plugin(plugin_dir, capsys):
    (plugin_dir / "lights.py").write_text(LIGHTS)
    config.register_plugins()
    assert config.PAD_ACTIONS["lights_hold"] == ("Desk lights: on while held", "", "")
    assert config.BUTTON_ACTIONS["lights_toggle"] == ("Desk lights: on/off", "Speed", "1-10")
    assert config.PAD_ACTIONS["mic_mute"] == ("Mute microphone", "", "")  # built-ins stay
    assert "bad id" not in config.PAD_ACTIONS and "no_label" not in config.PAD_ACTIONS
    assert "lights_hold" in config.HOLD_ACTIONS and "lights_toggle" not in config.HOLD_ACTIONS
    assert config.ON_COLOURS["lights_hold"] == (0, 160, 255)
    assert overlay.short_label(Action("lights_hold")) == "Lights (hold)"
    assert overlay.short_label(Action("lights_toggle")) == "Desk lights: on/off"
    assert log_lines(plugin_dir) == []  # nothing imported yet
    assert "already taken" in capsys.readouterr().err


def test_the_plugin_loads_on_first_use_and_holds_release(plugin_dir):
    (plugin_dir / "lights.py").write_text(LIGHTS)
    config.register_plugins()
    runner = actions.ActionRunner(None, osd=lambda: False, report=lambda _t: None, audio_changed=lambda: None)
    runner.press(Action("lights_hold"), "Pad 1")
    runner.release(Action("lights_hold"), "Pad 1")
    runner.press(Action("lights_toggle", " 5 "), "Pad 2")
    runner.release(Action("lights_toggle"), "Pad 2")  # not a hold: nothing
    assert log_lines(plugin_dir) == [
        "imported",
        json.dumps(["press", "lights_hold", ""]),
        json.dumps(["release", "lights_hold", ""]),
        json.dumps(["press", "lights_toggle", "5"]),
    ]
    assert plugins.marker_note() == "§4 …and that is why"


def test_a_broken_plugin_is_reported_and_skipped(plugin_dir, capsys):
    (plugin_dir / "boom.py").write_text(
        'ACTIONS = {"boom": {"label": "Boom"}}\nraise RuntimeError("nope")\ndef press(a, t): pass\n'
    )
    (plugin_dir / "raises.py").write_text(
        'ACTIONS = {"fail": {"label": "Fail"}}\ndef press(a, t):\n    raise ValueError("bad pad")\n'
    )
    (plugin_dir / "computed.py").write_text('ACTIONS = {"x" + "y": {"label": "XY"}}\ndef press(a, t): pass\n')
    (plugin_dir / "nopress.py").write_text('ACTIONS = {"np": {"label": "No press"}}\n')
    (plugin_dir / "syntax.py").write_text("ACTIONS = {\n")
    (plugin_dir / "_private.py").write_text('ACTIONS = {"priv": {"label": "P"}}\ndef press(a, t): pass\n')
    config.register_plugins()
    assert {"boom", "fail"} <= set(config.PAD_ACTIONS)
    assert not {"xy", "np", "priv"} & set(config.PAD_ACTIONS)
    runner = actions.ActionRunner(None, osd=lambda: False, report=lambda _t: None, audio_changed=lambda: None)
    runner.press(Action("boom"), "Pad 1")  # import fails: logged, Chimp carries on
    runner.press(Action("boom"), "Pad 1")  # and isn't retried
    runner.press(Action("fail"), "Pad 2")
    err = capsys.readouterr().err
    assert err.count("boom failed to load") == 1 and "RuntimeError: nope" in err
    assert "raises.press failed" in err and "ValueError: bad pad" in err
    assert "plain dict literal" in err and "no press(action, target)" in err


def test_a_package_plugin(plugin_dir):
    pkg = plugin_dir / "tools"
    pkg.mkdir()
    (pkg / "helper.py").write_text("def note():\n    return 'from a helper'\n")
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            """
            from .helper import note
            ACTIONS = {"tool": {"label": "Tool"}}
            def press(action, target):
                pass
            def marker_note():
                return note()
            """
        )
    )
    config.register_plugins()
    assert "tool" in config.PAD_ACTIONS
    assert plugins.marker_note() == "from a helper"


def test_a_missing_plugins_pads_survive_a_save(plugin_dir, tmp_path):
    # the plugin's gone (moved, broken): its pads keep their action, inert, until it's back
    cfg = DesktopConfig()
    cfg.actions(4).pads[2] = Action("lights_hold", "x")
    path = tmp_path / "desktop.json"
    cfg.save(path)
    again = DesktopConfig.load(path)
    assert again.actions(4).pads[2] == Action("lights_hold", "x")
    runner = actions.ActionRunner(None, osd=lambda: False, report=lambda _t: None, audio_changed=lambda: None)
    runner.press(again.actions(4).pads[2], "Pad 3")  # nothing to do, nothing fails
    runner.release(again.actions(4).pads[2], "Pad 3")
