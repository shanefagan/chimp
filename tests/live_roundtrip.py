"""Live write test: read program N, write the identical program back, read it again, compare.

Writes the same bytes the device already holds, so it changes nothing.
Run with: PYTHONPATH=. .venv/bin/python tests/live_roundtrip.py [slot]
"""

import sys

from PySide6.QtCore import QCoreApplication, QTimer

from chimp.device import Lpd8Device

SLOT = int(sys.argv[1]) if len(sys.argv) > 1 else 4
app = QCoreApplication(sys.argv)
dev = Lpd8Device()
reads = []


def on_conn(ok, _name):
    if ok:
        dev.request_program(SLOT)


def on_prog(slot, prog):
    if slot != SLOT:
        return
    reads.append(prog)
    if len(reads) == 1:
        print(f"read P{SLOT}, writing it back unchanged")
        dev.send_program(prog, SLOT)
        dev.request_program(SLOT)
    else:
        same = reads[0] == reads[1]
        print("read-back identical:", same)
        app.exit(0 if same else 2)


dev.connectionChanged.connect(on_conn)
dev.programReceived.connect(on_prog)
dev.requestTimedOut.connect(lambda what: print("TIMEOUT:", what))
dev.start()
QTimer.singleShot(6000, lambda: (print("gave up"), app.exit(1)))
rc = app.exec()
dev.stop()
sys.exit(rc)
