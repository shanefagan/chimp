"""MidiHub hotplug: ports that fail to open at first are retried, not forgotten."""

from typing import ClassVar

import pytest

rtmidi = pytest.importorskip("rtmidi")

from chimp.desktop import midi  # noqa: E402

PORT = "KO II:KO II MIDI 1 32:0"


class FakeIn:
    ports: ClassVar[list[str]] = []
    fail_opens = 0
    opened: ClassVar[list["FakeIn"]] = []

    def __init__(self, *_a, **_k):
        self.closed = False

    def get_ports(self):
        return list(FakeIn.ports)

    def ignore_types(self, **_k):
        pass

    def open_port(self, _idx, _name):
        if FakeIn.fail_opens:
            FakeIn.fail_opens -= 1
            raise rtmidi.SystemError("not ready")
        FakeIn.opened.append(self)

    def get_message(self):
        return None

    def close_port(self):
        self.closed = True

    def delete(self):
        pass


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setattr(midi.rtmidi, "MidiIn", FakeIn)
    FakeIn.ports, FakeIn.fail_opens, FakeIn.opened = [], 0, []
    return midi.MidiHub("Chimp test")


def test_port_that_fails_to_open_is_retried(hub):
    changes = []
    hub.devicesChanged.connect(lambda: changes.append(list(hub.devices)))
    FakeIn.ports = [PORT]
    FakeIn.fail_opens = 1  # still being set up on the first scan
    hub.scan()
    assert hub.devices == [] and changes == []
    hub.scan()
    assert hub.devices == ["KO II"] and changes == [["KO II"]]
    hub.scan()  # nothing new
    assert changes == [["KO II"]]
    FakeIn.ports = []
    hub.scan()
    assert hub.devices == [] and changes[-1] == []
    assert all(m.closed for m in FakeIn.opened)
