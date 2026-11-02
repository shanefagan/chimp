"""`chimp --version` and `chimp --check-install`, for packages' smoke tests.

The check needs no MIDI device, audio server or display: it imports every
module and compiles every QML file offscreen, which is where a missing
dependency (a PySide6 module, Kirigami, layer-shell-qt) shows up.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

HERE = Path(__file__).resolve().parent


def chimp_version() -> str:
    try:
        return version("chimp")
    except PackageNotFoundError:
        return "unknown"


def check_install() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    failures: list[str] = []

    import chimp

    for mod in pkgutil.walk_packages(chimp.__path__, "chimp."):
        if mod.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001 - report every failure, not just the first
            failures.append(f"import {mod.name}: {exc}")

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    if not os.environ.get("QT_QUICK_CONTROLS_STYLE"):
        QQuickStyle.setStyle("org.kde.desktop")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    engine = QQmlEngine()
    for qml in sorted((HERE / "qml").glob("*.qml")):
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml)))
        if component.isError():
            failures.extend(f"{qml.name}: {e.toString()}" for e in component.errors())
    del engine, app

    for line in failures:
        print(line, file=sys.stderr)
    if failures:
        return 1
    print(f"chimp {chimp_version()}: modules and QML load")
    return 0
