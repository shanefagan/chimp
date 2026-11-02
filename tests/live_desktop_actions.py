"""Exercise the desktop service with simulated LPD8 events; every change is undone.

Run with: PYTHONPATH=. .venv/bin/python tests/live_desktop_actions.py
"""

import re
import subprocess
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from chimp.desktop import system
from chimp.desktop.service import DesktopController

app = QCoreApplication(sys.argv)
ctl = DesktopController()
results = []


def vol(node):
    out = subprocess.run(["wpctl", "get-volume", str(node)], capture_output=True, text=True).stdout
    m = re.search(r"Volume: ([\d.]+)", out)
    return (float(m.group(1)) if m else None), "[MUTED]" in out


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS" if ok else "FAIL"), name, detail)


steps = []


def step(delay_ms):
    def deco(fn):
        steps.append((delay_ms, fn))
        return fn

    return deco


state = {}


@step(2500)
def connected():
    check("device connected", ctl.dev.connected)
    check("all 4 programs read from device", sorted(ctl.programs) == [1, 2, 3, 4], str(sorted(ctl.programs)))
    check("active program known", ctl.active in (1, 2, 3, 4), f"program {ctl.active}")
    state["mic"] = vol("@DEFAULT_AUDIO_SOURCE@")[1]
    ctl._on_note(16, 36, 100)  # pad 1 = mic mute
    ctl._on_note(16, 36, 0)


@step(600)
def mic_toggled():
    now = vol("@DEFAULT_AUDIO_SOURCE@")[1]
    check("pad 1 toggles mic mute", now != state["mic"], f"{state['mic']} -> {now}")
    check("pad 1 LED state follows mute", ctl.pad_state[0] == now)
    ctl._on_note(16, 36, 100)
    ctl._on_note(16, 36, 0)


@step(600)
def mic_restored():
    now = vol("@DEFAULT_AUDIO_SOURCE@")[1]
    check("second press restores mic", now == state["mic"])
    streams = system.streams_matching("brave")
    state["brave"] = [(s["id"], vol(s["id"])[0]) for s in streams]
    print("   brave streams:", state["brave"])
    ctl._on_cc(16, 72, 64)  # knob 3 = app volume brave


@step(400)
def brave_set():
    if not state["brave"]:
        check("knob 3 sets Brave volume", True, "(skipped: Brave not playing audio)")
        return
    got = [vol(i)[0] for i, _ in state["brave"]]
    check("knob 3 sets Brave volume", all(g is not None and abs(g - 0.50) < 0.02 for g in got), str(got))
    for i, v in state["brave"]:
        subprocess.run(["wpctl", "set-volume", str(i), str(v)])


@step(300)
def other_events_ignored():
    before = vol("@DEFAULT_AUDIO_SINK@")
    ctl.active = 1  # as if the device were on factory Program 1 (no actions)
    ctl._on_cc(1, 70, 10)  # knob 1 on Program 1: must not touch volume
    ctl._on_note(10, 36, 100)  # pad 1 on Program 1
    state["sink"] = before


@step(400)
def ignored_ok():
    check("events from programs without actions are ignored", vol("@DEFAULT_AUDIO_SINK@") == state["sink"])
    check(
        "display brightness targets found",
        len(system.displays()) == 3,
        ", ".join(d["label"] for d in system.displays()),
    )
    print("   OBS enabled:", ctl.obs.enabled, "connected:", ctl.obs.connected)
    app.quit()


def run_steps():
    t = 0
    for delay, fn in steps:
        t += delay
        QTimer.singleShot(t, fn)


ctl.start()
run_steps()
QTimer.singleShot(15000, lambda: (print("timeout"), app.exit(1)))
app.exec()
ctl.stop()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 2)
