"""System tray icon for the desktop service (Plasma shows it via StatusNotifierItem)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSize
from PySide6.QtGui import QActionGroup, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import system

ICON = Path(__file__).resolve().parents[1] / "icons" / "chimp.svg"


class Tray(QObject):
    def __init__(self, ctl, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        base = QIcon(str(ICON))
        self._base = base
        self._icon_on = base
        # Greyed-out variant for "not controlling anything right now"
        self._icon_off = QIcon()
        for size in (16, 22, 24, 32, 48, 64):
            self._icon_off.addPixmap(base.pixmap(QSize(size, size), QIcon.Mode.Disabled))

        self.tray = QSystemTrayIcon(self._icon_off, self)
        self.tray.activated.connect(self._on_activated)
        self._menu = QMenu()
        self.tray.setContextMenu(self._menu)
        self._signature = None

        ctl.stateChanged.connect(self.refresh)
        self.refresh()
        self.tray.show()

    def open_editor(self) -> None:
        system.spawn_app(["gtk-launch", "chimp"], "chimp")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_editor()

    def _set_overlay(self, on: bool) -> None:
        self.ctl.cfg.overlay = on
        self.ctl.cfg.save()  # the service's file watcher reloads it; so does an open editor
        self._signature = None
        self.refresh()

    # --- what to show ----------------------------------------------------------------

    def _lines(self) -> tuple[list[str], bool]:
        """Status lines per controller, and whether anything is controlling the desktop."""
        ctl = self.ctl
        lines, live = [], False
        if ctl.dev.connected:
            if not ctl.active:
                lines.append("LPD8 mk2: connected")
            elif ctl.cfg.actions(ctl.active).active():
                lines.append(f"LPD8 mk2: Program {ctl.active} · desktop control")
                live = True
            else:
                lines.append(f"LPD8 mk2: Program {ctl.active} · MIDI only")
        for name in ctl.generic_devices:
            dev = ctl.cfg.devices.get(name)
            if dev is None or not any(c.action.type != "none" for pr in dev.profiles for c in pr.controls):
                lines.append(f"{name}: not set up")
            elif not dev.enabled:
                lines.append(f"{name}: off")
            else:
                prof = dev.profiles[ctl.current_profile_index(name)]
                lines.append(f"{name}: {prof.name}")
                live = live or any(c.action.type != "none" for c in prof.controls)
        if not lines:
            lines.append("No MIDI controllers connected")
        if ctl.paused:
            live = False
        return lines, live

    def refresh(self) -> None:
        ctl = self.ctl
        lines, live = self._lines()
        last = (ctl._status.get("last") or {}).get("text", "")
        multi = [
            (n, ctl.cfg.devices[n])
            for n in ctl.generic_devices
            if n in ctl.cfg.devices and len(ctl.cfg.devices[n].profiles) > 1
        ]
        signature = (
            ctl.cfg.overlay,
            tuple(lines),
            last,
            ctl.paused,
            tuple((n, ctl.current_profile_index(n), tuple(p.name for p in d.profiles)) for n, d in multi),
        )
        self.tray.setIcon(self._icon_on if live else self._icon_off)
        self.tray.setToolTip(
            "Chimp\n" + ("Paused\n" if ctl.paused else "") + "\n".join(lines) + (f"\nLast: {last}" if last else "")
        )
        if signature == self._signature:
            return
        self._signature = signature
        self._rebuild(lines, last, multi)

    def _rebuild(self, lines: list[str], last: str, multi) -> None:
        menu = self._menu
        menu.clear()
        for line in lines:
            menu.addAction(line).setEnabled(False)
        if last:
            menu.addAction(f"Last: {last}").setEnabled(False)
        for name, dev in multi:
            sub = menu.addMenu(f"{name} profile")
            group = QActionGroup(sub)
            current = self.ctl.current_profile_index(name)
            for i, prof in enumerate(dev.profiles):
                act = sub.addAction(prof.name)
                act.setCheckable(True)
                act.setChecked(i == current)
                group.addAction(act)
                act.triggered.connect(lambda _=False, n=name, i=i: self.ctl.set_profile(n, i))
        menu.addSeparator()
        ov = menu.addAction("Show overlay while using a controller")
        ov.setCheckable(True)
        ov.setChecked(self.ctl.cfg.overlay)
        ov.toggled.connect(self._set_overlay)
        pause = menu.addAction("Pause desktop control")
        pause.setCheckable(True)
        pause.setChecked(self.ctl.paused)
        pause.toggled.connect(self.ctl.set_paused)
        menu.addAction(QIcon.fromTheme("chimp", self._base), "Open editor", self.open_editor)
        menu.addSeparator()
        menu.addAction(QIcon.fromTheme("application-exit"), "Quit until next login", QApplication.quit)
