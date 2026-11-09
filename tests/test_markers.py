"""Markers, and pad releases."""

from __future__ import annotations

from datetime import datetime

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from chimp.desktop import markers
from chimp.desktop.config import Action, DesktopConfig
from chimp.desktop.service import DesktopController

T0 = datetime(2026, 11, 9, 20, 15, 3).timestamp()


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


class FakeObs(QObject):
    recordingStarted = Signal(int)
    recordingStopped = Signal(str)

    def __init__(self):
        super().__init__()
        self.connected = True
        self.recording = False
        self.requests = []  # (type, callback)

    def request(self, kind, data=None, callback=None):
        self.requests.append((kind, callback))
        return f"{kind}:{len(self.requests)}"

    def answer(self, kind, ok, data):
        for i, (k, cb) in enumerate(self.requests):
            if k == kind and cb is not None:
                del self.requests[i]
                cb(ok, data)
                return
        raise AssertionError(f"no {kind} request")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _markers(tmp_path, obs, clock, where=""):
    reports = []
    m = markers.Markers(
        obs, reports.append, position=lambda: where, now=clock, markers_dir=lambda: tmp_path / "Markers"
    )
    return m, reports


def _lines(path):
    return [line.split("\t") for line in path.read_text().splitlines()]


def test_outside_a_recording_markers_go_in_a_daily_file_at_the_time_of_day(tmp_path):
    obs, clock = FakeObs(), Clock(T0)
    m, reports = _markers(tmp_path, obs, clock, where="§2 …the bridge…")
    m.add("retake")
    clock.t += 1.5
    m.add()
    path = tmp_path / "Markers" / "2026-11-09.markers.txt"
    (a, b) = _lines(path)
    seconds = 20 * 3600 + 15 * 60 + 3
    assert a == [f"{seconds:.3f}", f"{seconds:.3f}", "Marker 1 · 20:15:03 · retake · §2 …the bridge…"]
    assert b[0] == f"{seconds + 1.5:.3f}" and b[2].startswith("Marker 2 · 20:15:04")
    assert reports[-1] == f"Marker 2 → {path}"


def test_while_recording_the_time_is_obs_s_and_the_file_ends_up_next_to_the_video(tmp_path):
    obs, clock = FakeObs(), Clock(T0)
    m, reports = _markers(tmp_path, obs, clock)
    obs.recording = True
    obs.recordingStarted.emit(0)
    obs.answer("GetRecordDirectory", True, {"recordDirectory": str(tmp_path / "rec")})

    clock.t = T0 + 75.0
    m.add("flubbed it")
    clock.t += 0.05  # OBS answers 50 ms later; it was paused for 10 s, so 65 s in at the press
    obs.answer("GetRecordStatus", True, {"outputActive": True, "outputDuration": 65_050})
    path = tmp_path / "rec" / "2026-11-09 20-15-03.markers.txt"
    ((start, end, label),) = _lines(path)
    assert start == end == "65.000"
    assert label == "Marker 1 · 20:16:18 · flubbed it"
    assert reports[-1] == f"Marker 1 at 01:05.000 → {path.name}"

    # OBS's default file name is the start time too, so the markers already match the video.
    reports.clear()
    obs.recording = False
    obs.recordingStopped.emit(str(tmp_path / "rec" / "2026-11-09 20-15-03.mkv"))
    assert path.exists()
    assert reports == []


def test_markers_are_renamed_after_the_recording(tmp_path):
    obs, clock = FakeObs(), Clock(T0)
    m, reports = _markers(tmp_path, obs, clock)
    obs.recording = True
    obs.recordingStarted.emit(0)
    obs.answer("GetRecordDirectory", True, {"recordDirectory": str(tmp_path)})
    clock.t += 3
    m.add()
    obs.answer("GetRecordStatus", True, {"outputDuration": 3000})
    written = tmp_path / "2026-11-09 20-15-03.markers.txt"
    assert written.exists()

    obs.recording = False
    obs.recordingStopped.emit(str(tmp_path / "My take.mkv"))
    assert not written.exists()
    assert _lines(tmp_path / "My take.markers.txt")[0][0] == "3.000"
    assert reports[-1] == f"Markers saved as {tmp_path / 'My take.markers.txt'}"

    # The next recording starts a new file.
    clock.t += 60
    obs.recording = True
    obs.recordingStarted.emit(0)
    m.add()
    obs.answer("GetRecordStatus", True, {"outputDuration": 0})
    assert (tmp_path / "Markers" / "2026-11-09 20-16-06.markers.txt").exists()  # before OBS said where


def test_no_answer_from_obs_works_it_out(tmp_path, monkeypatch):
    obs, clock = FakeObs(), Clock(T0)
    m, _ = _markers(tmp_path, obs, clock)
    timers = []
    monkeypatch.setattr(markers.QTimer, "singleShot", lambda ms, fn: timers.append(fn))
    obs.recording = True
    obs.recordingStarted.emit(12_000)  # Chimp connected to OBS 12 s into a recording
    clock.t += 8
    m.add()
    (fallback,) = timers
    fallback()
    obs.answer("GetRecordStatus", True, {"outputDuration": 99_000})  # too late: ignored
    (only,) = _lines(next((tmp_path / "Markers").glob("*.markers.txt")))
    assert only[0] == "20.000"


def test_lpd8_pad_release_reaches_the_runner_even_when_paused():
    events = []

    class Runner:
        def press(self, a, who):
            events.append(("press", a.type))

        def release(self, a, who):
            events.append(("release", a.type))

    class Overlay:
        def touch_lpd8(self, *_):
            pass

    ctl = DesktopController.__new__(DesktopController)
    ctl.cfg = DesktopConfig()
    ctl.cfg.actions(4).pads[0] = Action("marker")
    ctl.runner, ctl.overlay, ctl.paused, ctl._informed = Runner(), Overlay(), False, set()
    ctl._release_timer = type("T", (), {"start": lambda self: None})()
    ctl._resolve = lambda kind, ch, n: (4, 0)
    ctl._pad_event("note", 10, 36, True)
    ctl.paused = True
    ctl._pad_event("note", 10, 36, False)
    ctl._pad_event("note", 10, 36, True)  # paused: no press
    assert events == [("press", "marker"), ("release", "marker")]
