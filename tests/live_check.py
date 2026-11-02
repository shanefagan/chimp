"""Live smoke test against a plugged-in LPD8 mk2: connect, read all 4 programs + active slot.

Read-only: never sends a program. Run with: .venv/bin/python tests/live_check.py
"""

import sys

from PySide6.QtCore import QCoreApplication, QTimer

from chimp.device import Lpd8Device

app = QCoreApplication(sys.argv)
dev = Lpd8Device()
got: dict[int, object] = {}
state = {"active": None}


def on_conn(ok, name):
    print("connection:", ok, repr(name))
    if ok:
        for s in range(1, 5):
            dev.request_program(s)
        dev.request_active_program()


def on_prog(slot, prog):
    got[slot] = prog
    pads = prog.pads
    print(
        f"P{slot}: ch{prog.global_channel} pressure={prog.pressure} full={prog.full_level} toggle={prog.toggle} "
        f"notes={[p.note for p in pads]} padch={pads[0].channel} off={pads[0].off_color} on={pads[0].on_color} "
        f"knobs={[k.cc for k in prog.knobs]}"
    )
    maybe_done()


def on_active(slot):
    state["active"] = slot
    print("active program:", slot)
    maybe_done()


def maybe_done():
    if len(got) == 4 and state["active"] is not None:
        app.quit()


dev.connectionChanged.connect(on_conn)
dev.programReceived.connect(on_prog)
dev.activeProgramReceived.connect(on_active)
dev.requestTimedOut.connect(lambda what: print("TIMEOUT:", what))
dev.start()
QTimer.singleShot(5000, lambda: (print("gave up"), app.exit(1)))
rc = app.exec()
dev.stop()
sys.exit(rc)
