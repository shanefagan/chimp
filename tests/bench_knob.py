"""Benchmark: a fast knob sweep through the real action code, plus 10 s of audio-state upkeep.

Sweeps mic volume (silent) with on-screen display on, as in normal use, then puts the
mic back where it was. Counts processes spawned and CPU used (ours + children).

Run with: PYTHONPATH=. .venv/bin/python tests/bench_knob.py
"""

import os
import re
import subprocess
import sys
import time

from PySide6.QtCore import QCoreApplication, QTimer

from chimp.desktop import system
from chimp.desktop.actions import ActionRunner
from chimp.desktop.config import Action

spawned = {"n": 0}
_popen = subprocess.Popen.__init__


def counting_init(self, *a, **kw):
    spawned["n"] += 1
    _popen(self, *a, **kw)


subprocess.Popen.__init__ = counting_init


class NoObs:
    connected = False


def mic():
    out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"], capture_output=True, text=True).stdout
    return re.search(r"Volume: ([\d.]+)", out).group(1)


app = QCoreApplication(sys.argv)
original = mic()
spawned["n"] = 0
runner = ActionRunner(NoObs(), osd=lambda: True, report=lambda _t: None, audio_changed=lambda: None)
action = Action("mic_volume")
EVENTS, SECONDS = 200, 2.0
t0 = os.times()
start = time.monotonic()


def send(i):
    runner.level("k", action, i / EVENTS, i, "bench")


for i in range(EVENTS):
    QTimer.singleShot(int(i * SECONDS * 1000 / EVENTS), lambda i=i: send(i))
QTimer.singleShot(int(SECONDS * 1000) + 300, app.quit)
app.exec()
t1 = os.times()
cpu_self = (t1.user - t0.user) + (t1.system - t0.system)
cpu_kids = (t1.children_user - t0.children_user) + (t1.children_system - t0.children_system)
print(
    f"knob sweep: {EVENTS} MIDI events in {SECONDS:.0f} s -> {spawned['n']} processes spawned, "
    f"CPU {cpu_self * 1000:.0f} ms in-process + {cpu_kids * 1000:.0f} ms in children"
)

# Mute-state upkeep: what keeping pad lights in sync costs over 10 s
spawned["n"] = 0
t0 = os.times()
for _ in range(10):
    system.is_muted(system.SOURCE)
    system.is_muted(system.SINK)
t1 = os.times()
print(
    f"mute polling (current design, 10 s): {spawned['n']} processes, "
    f"{((t1.children_user - t0.children_user) + (t1.children_system - t0.children_system)) * 1000:.0f} ms CPU in children"
)

subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", original])
print("mic restored to", mic())
