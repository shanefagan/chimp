"""Listens to every MIDI controller on the ALSA sequencer (hotplug aware).

Each device is identified by its sequencer client name ("LPD8 mk2", "EP-133",
"Arturia MicroFreak", ...). Messages are drained on the Qt thread by a timer.
"""

from __future__ import annotations

import contextlib

import rtmidi
from PySide6.QtCore import QObject, QTimer, Signal

IGNORED_CLIENTS = {"System", "Midi Through", "PipeWire-System", "PipeWire-RT-Event", "Announce"}
OWN_PREFIXES = ("Chimp", "KnobDeck", "LPD8 mk2 ")  # our own clients ("Chimp", "Chimp Service"; earlier names)


def client_of(port_name: str) -> str:
    return port_name.split(":", 1)[0]


def is_controller(client: str, exclude: set[str]) -> bool:
    return not (
        client in IGNORED_CLIENTS or client in exclude or client.startswith((*OWN_PREFIXES, "PipeWire", "rtmidi"))
    )


class MidiHub(QObject):
    devicesChanged = Signal()
    message = Signal(str, object)  # device name, [status, data1, data2]

    def __init__(self, client_name: str, exclude: set[str] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client_name = client_name
        self._exclude = set(exclude or ())
        self._probe = rtmidi.MidiIn(rtmidi.API_LINUX_ALSA, name=f"{client_name} (probe)")
        self._open: dict[str, list[rtmidi.MidiIn]] = {}
        self._scan_timer = QTimer(self, interval=1500, timeout=self.scan)
        self._drain_timer = QTimer(self, interval=10, timeout=self._drain)

    def start(self) -> None:
        self.scan()
        self._scan_timer.start()
        self._drain_timer.start()

    def stop(self) -> None:
        self._scan_timer.stop()
        self._drain_timer.stop()
        for name in list(self._open):
            self._close(name)

    @property
    def devices(self) -> list[str]:
        return sorted(self._open)

    def scan(self) -> None:
        try:
            ports = self._probe.get_ports()
        except rtmidi.SystemError:
            return
        present: dict[str, list[str]] = {}
        for port in ports:
            client = client_of(port)
            if is_controller(client, self._exclude):
                present.setdefault(client, []).append(port)
        changed = False
        for name in list(self._open):
            if name not in present:
                self._close(name)
                changed = True
        for name, port_names in present.items():
            if name not in self._open:
                self._open_device(name, port_names)
                changed = True
        if changed:
            self.devicesChanged.emit()

    def _open_device(self, name: str, port_names: list[str]) -> None:
        opened = []
        for port in port_names:
            try:
                m = rtmidi.MidiIn(rtmidi.API_LINUX_ALSA, name=self._client_name, queue_size_limit=2048)
                m.ignore_types(sysex=True, timing=True, active_sense=True)
                idx = m.get_ports().index(port)
                m.open_port(idx, f"from {name}"[:60])
                opened.append(m)
            except (ValueError, rtmidi.SystemError, rtmidi.InvalidPortError):
                continue
        self._open[name] = opened

    def _close(self, name: str) -> None:
        for m in self._open.pop(name, []):
            with contextlib.suppress(rtmidi.RtMidiError, RuntimeError):
                m.close_port()
                m.delete()

    def _drain(self) -> None:
        for name, ports in list(self._open.items()):
            for m in ports:
                for _ in range(256):
                    try:
                        item = m.get_message()
                    except rtmidi.SystemError:
                        item = None
                    if item is None:
                        break
                    if item[0]:
                        self.message.emit(name, item[0])
