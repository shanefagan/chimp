"""Live write test that proves the write lands: tweak knob 8 max on program N, read back,
then restore the original and read back again.

Run with: PYTHONPATH=. .venv/bin/python tests/live_write_check.py [slot]
"""

import sys

from PySide6.QtCore import QCoreApplication, QTimer

from chimp.device import Lpd8Device

SLOT = int(sys.argv[1]) if len(sys.argv) > 1 else 4
app = QCoreApplication(sys.argv)
dev = Lpd8Device()
state = {"step": 0, "orig": None, "ok": True}


def on_conn(ok, _name):
    if ok:
        dev.request_program(SLOT)


def on_prog(slot, prog):
    if slot != SLOT:
        return
    step = state["step"]
    if step == 0:
        state["orig"] = prog.copy()
        tweaked = prog.copy()
        tweaked.knobs[7].max = 126 if prog.knobs[7].max != 126 else 125
        state["want"] = tweaked.knobs[7].max
        print(f"original knob 8 max={prog.knobs[7].max}; writing {state['want']}")
        dev.send_program(tweaked, SLOT)
        dev.request_program(SLOT)
    elif step == 1:
        got = prog.knobs[7].max
        print(f"read back knob 8 max={got} (expected {state['want']})")
        state["ok"] &= got == state["want"]
        dev.send_program(state["orig"], SLOT)
        dev.request_program(SLOT)
    else:
        restored = prog == state["orig"]
        print("restored original:", restored)
        state["ok"] &= restored
        app.exit(0 if state["ok"] else 2)
    state["step"] += 1


dev.connectionChanged.connect(on_conn)
dev.programReceived.connect(on_prog)
dev.requestTimedOut.connect(lambda what: print("TIMEOUT:", what))
dev.start()
QTimer.singleShot(8000, lambda: (print("gave up"), app.exit(1)))
rc = app.exec()
dev.stop()
sys.exit(rc)
