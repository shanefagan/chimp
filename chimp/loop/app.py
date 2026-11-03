"""Chimp Loop: `chimp-loop` / `python -m chimp.loop`."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RATE = 48000
BUFFER_FRAMES = 512
PERIOD_FRAMES = 256
BUS_NAME = "com.performativenonsense.chimp.loop"


def main() -> int:
    if "--version" in sys.argv:
        from ..selfcheck import chimp_version

        print(f"chimp-loop {chimp_version()}")
        return 0
    # Ask PipeWire (natively or through its Pulse server) for a short period, for this
    # process only: with the default 1024-frame graph period a pad takes ~40 ms to sound.
    os.environ.setdefault("PIPEWIRE_LATENCY", f"{PERIOD_FRAMES}/{RATE}")
    os.environ.setdefault("PULSE_LATENCY_MSEC", str(round(PERIOD_FRAMES / RATE * 1000)))
    if not os.environ.get("QT_QUICK_CONTROLS_STYLE"):
        from PySide6.QtQuickControls2 import QQuickStyle

        QQuickStyle.setStyle("org.kde.desktop")

    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtDBus import QDBusConnection
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine

    from .controller import LoopController
    from .engine import Engine
    from .output import AudioOut

    app = QGuiApplication(sys.argv)
    app.setApplicationName("Chimp Loop")
    app.setOrganizationName("chimp")
    app.setDesktopFileName("chimp-loop")
    app.setWindowIcon(QIcon(str(HERE.parent / "icons" / "chimp.svg")))

    # One looper at a time; the desktop service sees this name and leaves the
    # controllers to us (except the LPD8's desktop program).
    bus = QDBusConnection.sessionBus()
    if bus.isConnected() and not bus.registerService(BUS_NAME):
        print("Chimp Loop is already running", file=sys.stderr)
        return 1

    engine = Engine(RATE)
    ctl = LoopController(engine)
    out = AudioOut(engine, buffer_frames=BUFFER_FRAMES)
    # Recorded hits are placed where they were heard: our buffer plus PipeWire's period.
    out.started.connect(lambda frames, device: ctl.set_latency(frames + PERIOD_FRAMES, device))
    out.started.connect(lambda frames, _d: setattr(engine, "latency", frames + PERIOD_FRAMES))
    out.failed.connect(lambda msg: ctl.notify.emit(f"No sound: {msg}"))

    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("loop", ctl)
    qml.load(QUrl.fromLocalFile(str(HERE.parent / "qml" / "Loop.qml")))
    if not qml.rootObjects():
        return 1

    out.start()
    ctl.start()
    if os.environ.get("CHIMP_LOOP_OPEN"):  # dev aid: open a saved loop
        ctl.loadLoop(os.environ["CHIMP_LOOP_OPEN"])
    if os.environ.get("CHIMP_LOOP_VIEW"):  # dev aid: "pads" or "steps"
        qml.rootObjects()[0].setProperty("view", os.environ["CHIMP_LOOP_VIEW"])
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer(interval=250, timeout=lambda: None)  # lets Python see Ctrl+C
    tick.start()

    shot = os.environ.get("CHIMP_SCREENSHOT")  # dev aid: grab the window and exit
    if shot:
        import shiboken6
        from PySide6.QtQuick import QQuickWindow

        win = shiboken6.wrapInstance(shiboken6.getCppPointer(qml.rootObjects()[0])[0], QQuickWindow)
        QTimer.singleShot(
            int(os.environ.get("CHIMP_SCREENSHOT_DELAY_MS", "2500")),
            lambda: (win.grabWindow().save(shot), app.quit()),
        )

    rc = app.exec()
    ctl.stop()
    out.stop()
    del qml
    if bus.isConnected():
        bus.unregisterService(BUS_NAME)
    return rc
