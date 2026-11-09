"""Markers: note a moment while recording, to find it again when editing.

Each marker is a line in a text file, in Audacity's label format (start, end, label,
tab-separated; times in seconds), so a file can be imported as a label track
(File > Import > Labels) or read by a script that cuts the recording:

    134.480	134.480	Marker 3 · 20:17:21 · retake · §4 …and that is why the…

- While OBS records, the time is the position in the recording, and the file sits next to
  the video: "<recording>.markers.txt" (named after the recording's start time until OBS
  says what the file is called, when it stops).
- Otherwise the markers go to ~/Videos/Markers/<date>.markers.txt and the time is the
  time of day (seconds since midnight).

The label has the marker's number, the clock time, the pad's note (if any) and anything
your plugins add (a marker_note(), e.g. where a teleprompter is in the script).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from . import plugins

# How long to wait for OBS to say where the recording is before working it out ourselves.
OBS_ANSWER_MS = 1000


def videos_dir() -> Path:
    """The XDG videos folder (~/Videos unless user-dirs.dirs says otherwise)."""
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "user-dirs.dirs"
    try:
        for line in config.read_text().splitlines():
            m = re.match(r'\s*XDG_VIDEOS_DIR="(.*)"', line)
            if m:
                return Path(m.group(1).replace("$HOME", str(Path.home())))
    except OSError:
        pass
    return Path.home() / "Videos"


def append(path: Path, seconds: float, label: str) -> int:
    """Add a marker line; returns its number in the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        number = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) + 1
    except OSError:
        number = 1
    label = re.sub(r"\s+", " ", f"Marker {number} · {label}").strip()
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{seconds:.3f}\t{seconds:.3f}\t{label}\n")
    return number


def clock(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:06.3f}" if h else f"{m:02d}:{s:06.3f}"


class Markers(QObject):
    def __init__(
        self,
        obs,
        report: Callable[[str], None],
        position: Callable[[], str] = plugins.marker_note,
        now: Callable[[], float] = time.time,
        markers_dir: Callable[[], Path] = lambda: videos_dir() / "Markers",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.obs = obs
        self._report = report  # (summary) once the marker is written
        self._position = position
        self._now = now
        self._markers_dir = markers_dir
        self._rec_start: float | None = None  # wall clock when the current recording started
        self._rec_dir: Path | None = None  # where OBS writes it
        self._rec_file: Path | None = None  # markers for it, once there is one
        if hasattr(obs, "recordingStarted"):  # (benchmarks use a stand-in without OBS)
            obs.recordingStarted.connect(self._started)
            obs.recordingStopped.connect(self._stopped)

    # --- OBS recording ---------------------------------------------------------------

    def _started(self, running_ms: int) -> None:
        self._rec_start = self._now() - running_ms / 1000
        self._rec_dir = self._rec_file = None
        self.obs.request("GetRecordDirectory", callback=self._got_dir)

    def _got_dir(self, ok: bool, data: dict) -> None:
        if ok and data.get("recordDirectory"):
            self._rec_dir = Path(data["recordDirectory"])

    def _stopped(self, output_path: str) -> None:
        """Name the markers after the recording, next to it."""
        markers, self._rec_file, self._rec_start = self._rec_file, None, None
        if markers is None or not output_path or not markers.exists():
            return
        target = Path(output_path).with_suffix(".markers.txt")
        if target != markers and not target.exists():
            try:
                markers.replace(target)
            except OSError:
                return
            self._report(f"Markers saved as {target}")

    # --- adding --------------------------------------------------------------------

    def add(self, note: str = "") -> None:
        pressed = self._now()
        where = self._position()  # first: a plugin may react to the press (a teleprompter flashes)
        label = " · ".join(x for x in (datetime.fromtimestamp(pressed).strftime("%H:%M:%S"), note.strip(), where) if x)
        if not (self.obs.connected and self.obs.recording and self._rec_start is not None):
            midnight = datetime.fromtimestamp(pressed).replace(hour=0, minute=0, second=0, microsecond=0)
            path = self._markers_dir() / f"{midnight:%Y-%m-%d}.markers.txt"
            n = append(path, pressed - midnight.timestamp(), label)
            self._report(f"Marker {n} → {path}")
            return

        # Recording: ask OBS how far in it is (that leaves out pauses), else work it out.
        done = False

        def write(seconds: float) -> None:
            nonlocal done
            if done:
                return
            done = True
            path = self._recording_file()
            n = append(path, seconds, label)
            self._report(f"Marker {n} at {clock(seconds)} → {path.name}")

        def answer(ok: bool, data: dict) -> None:
            if ok and data.get("outputDuration") is not None:
                write(data["outputDuration"] / 1000 - (self._now() - pressed))
            else:
                write(pressed - (self._rec_start or pressed))

        start = self._rec_start
        if self.obs.request("GetRecordStatus", callback=answer):
            QTimer.singleShot(OBS_ANSWER_MS, lambda: write(pressed - start))
        else:
            write(pressed - start)

    def _recording_file(self) -> Path:
        if self._rec_file is None:
            started = datetime.fromtimestamp(self._rec_start or self._now())
            folder = self._rec_dir or self._markers_dir()
            self._rec_file = folder / f"{started:%Y-%m-%d %H-%M-%S}.markers.txt"
        return self._rec_file
