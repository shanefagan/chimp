"""Entry point: python -m chimp"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from .backend import Backend
from .desktop.bridge import DesktopBridge

HERE = Path(__file__).resolve().parent


def main() -> int:
    from . import selfcheck
    from .desktop.config import migrate_legacy

    if "--version" in sys.argv:
        print(f"chimp {selfcheck.chimp_version()}")
        return 0
    if "--check-install" in sys.argv:
        return selfcheck.check_install()
    migrate_legacy()
    if not os.environ.get("QT_QUICK_CONTROLS_STYLE"):
        QQuickStyle.setStyle("org.kde.desktop")

    app = QGuiApplication(sys.argv)
    app.setApplicationName("Chimp")
    app.setOrganizationName("chimp")
    app.setDesktopFileName("chimp")
    app.setWindowIcon(QIcon(str(HERE / "icons" / "chimp.svg")))

    backend = Backend()
    if "--program" in sys.argv:  # open on a given program tab: --program 4
        with contextlib.suppress(IndexError, ValueError):
            backend.currentSlot = int(sys.argv[sys.argv.index("--program") + 1])
    engine = QQmlApplicationEngine()
    desktop = DesktopBridge(backend)
    if "--device" in sys.argv:  # open on a given MIDI device: --device "EP-133"
        with contextlib.suppress(IndexError):
            desktop.setCurrentDevice(sys.argv[sys.argv.index("--device") + 1])
    desktop.notify.connect(backend.notify)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("desktop", desktop)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    if not engine.rootObjects():
        return 1

    backend.start()
    app.aboutToQuit.connect(backend.shutdown)
    app.aboutToQuit.connect(desktop.shutdown)

    # Let Ctrl+C in a terminal close the app cleanly.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer(interval=250, timeout=lambda: None)
    tick.start()

    # Dev aid: LPD8_SCREENSHOT=/path.png grabs the window after a few seconds and exits.
    shot = os.environ.get("LPD8_SCREENSHOT")
    if shot:
        window = engine.rootObjects()[0]

        def grab() -> None:
            window.grabWindow().save(shot)
            app.quit()

        QTimer.singleShot(int(os.environ.get("LPD8_SCREENSHOT_DELAY_MS", "3500")), grab)

    rc = app.exec()
    # Tear QML down while the Python objects it binds to are still alive.
    del engine
    return rc
