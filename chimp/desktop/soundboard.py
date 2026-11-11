"""Soundboard: pads and buttons that play sound clips (the "Play sound" action).

Each clip is decoded once (WAV directly; MP3, OGG, FLAC, Opus... through Qt's decoder)
into 48 kHz float stereo and kept in memory, so a press starts it straight away. Clips
are mixed in their own audio thread, one stream per output device, opened when a clip
plays there and closed once it has been quiet for a while.

How a press behaves is the action's mode (its options):

- oneshot: plays to the end; pressing again starts another copy over it
- toggle:  press plays, press again stops
- restart: each press starts it again from the top
- hold:    plays while the pad is held, stops when it's let go

Stops fade out over a few milliseconds, so nothing clicks.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QIODevice, QMetaObject, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot

RATE = 48000
BYTES_PER_FRAME = 8  # float32 stereo
BUFFER_FRAMES = 2048  # ~43 ms: roomy enough that the service's other work can't starve it
BLOCK = 1024  # frames rendered per pull, at most
FADE = 256  # frames a stop takes (~5 ms)
IDLE_CLOSE_S = 20.0  # an output stream closes after this long with nothing playing
MAX_SECONDS = 600  # longer files are cut here (10 minutes is 230 MB in memory)

MODES: dict[str, str] = {
    "oneshot": "Tap: play it through (presses overlap)",
    "toggle": "Tap: play, tap again: stop",
    "restart": "Tap: play from the start",
    "hold": "Hold: plays while held",
}
DEFAULT_MODE = "oneshot"
AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac", ".wma", ".aiff", ".aif")


@dataclass(frozen=True)
class Settings:
    """A "Play sound" action's options, checked."""

    mode: str = DEFAULT_MODE
    output: str = ""  # an output device's description; "" = the system's default
    volume: int = 100  # percent

    @property
    def gain(self) -> float:
        return (self.volume / 100) ** 2  # a perceptual curve: 50% is about -12 dB


def settings(options: dict | None) -> Settings:
    o = options or {}
    mode = o.get("mode") if o.get("mode") in MODES else DEFAULT_MODE
    output = o.get("output") if isinstance(o.get("output"), str) else ""
    vol = o.get("volume", 100)
    volume = max(0, min(100, int(vol))) if isinstance(vol, (int, float)) and not isinstance(vol, bool) else 100
    return Settings(mode, output.strip(), volume)


def clip_path(target: str) -> Path:
    return Path(target.strip()).expanduser()


def clip_label(target: str) -> str:
    """A clip's name for a pad: its file name without the extension."""
    return clip_path(target).stem.replace("_", " ") if target.strip() else ""


# --- the mix ---------------------------------------------------------------------------


@dataclass
class Voice:
    key: str  # which pad started it (toggle, restart and hold act on their own voices)
    data: np.ndarray  # float32 (frames, 2)
    gain: float
    pos: int = 0
    fade: int = -1  # frames left of a fade-out; -1 = not stopping

    def stop(self) -> None:
        if self.fade < 0:
            self.fade = FADE


@dataclass
class Mixer:
    """The clips playing on one output. render() runs on the audio thread; the rest on
    the service's. A lock keeps them apart (held for microseconds)."""

    voices: list[Voice] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    quiet_since: float = field(default_factory=time.monotonic)
    ended: Callable[[], None] | None = None  # called (audio thread) when voices finish

    def add(self, v: Voice) -> None:
        with self.lock:
            self.voices.append(v)

    def stop(self, key: str | None = None) -> int:
        """Fade out the voices `key` started (all, for None); how many were playing."""
        n = 0
        with self.lock:
            for v in self.voices:
                if (key is None or v.key == key) and v.fade < 0:
                    v.stop()
                    n += 1
        return n

    def playing(self) -> set[str]:
        with self.lock:
            return {v.key for v in self.voices if v.fade < 0}

    def render(self, n: int) -> np.ndarray:
        out = np.zeros((n, 2), np.float32)
        finished = False
        with self.lock:
            keep = []
            for v in self.voices:
                take = min(n, len(v.data) - v.pos)
                if v.fade >= 0:
                    take = min(take, v.fade)
                if take > 0:
                    chunk = v.data[v.pos : v.pos + take] * v.gain
                    if v.fade >= 0:
                        ramp = (np.arange(v.fade, v.fade - take, -1, dtype=np.float32) / FADE)[:, None]
                        chunk = chunk * ramp
                        v.fade -= take
                    out[:take] += chunk
                    v.pos += take
                if v.pos < len(v.data) and v.fade != 0:
                    keep.append(v)
                else:
                    finished = True
            self.voices = keep
            if keep:
                self.quiet_since = time.monotonic()
        if finished and self.ended is not None:
            self.ended()
        np.clip(out, -1.0, 1.0, out=out)
        return out


# --- an output stream ----------------------------------------------------------------


class _Source(QIODevice):
    def __init__(self, mixer: Mixer) -> None:
        super().__init__()
        self._mixer = mixer
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return BLOCK * BYTES_PER_FRAME * 4 + super().bytesAvailable()

    def readData(self, maxlen: int):
        n = min(maxlen // BYTES_PER_FRAME, BLOCK)
        return self._mixer.render(n).tobytes() if n > 0 else b""

    def writeData(self, _data) -> int:
        return -1


class _Worker(QObject):
    failed = Signal(str)

    def __init__(self, mixer: Mixer, device: str) -> None:
        super().__init__()
        self._mixer, self._device = mixer, device
        self._sink = None
        self._source: _Source | None = None

    @Slot()
    def open(self) -> None:
        if self._sink is not None:
            return
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        device = QMediaDevices.defaultAudioOutput()
        if self._device:
            device = next((d for d in QMediaDevices.audioOutputs() if d.description() == self._device), device)
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        if device.isNull():
            self.failed.emit("no audio output")
            return
        self._sink = QAudioSink(device, fmt)
        self._sink.setBufferSize(BUFFER_FRAMES * BYTES_PER_FRAME)
        self._source = _Source(self._mixer)
        self._sink.start(self._source)

    @Slot()
    def close(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        if self._source is not None:
            self._source.close()
            self._source = None

    @Slot(result=bool)
    def is_open(self) -> bool:
        return self._sink is not None


class Output(QObject):
    """One output device's stream, in its own thread."""

    def __init__(self, device: str, mixer: Mixer, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.device, self.mixer = device, mixer
        self._open = False
        self._thread = QThread()
        self._thread.setObjectName("chimp-soundboard")
        self._worker = _Worker(mixer, device)
        self._worker.moveToThread(self._thread)
        self.failed = self._worker.failed

    def ensure_open(self) -> None:
        if not self._thread.isRunning():
            self._thread.start()
            self._thread.setPriority(QThread.Priority.TimeCriticalPriority)
        if not self._open:
            QMetaObject.invokeMethod(self._worker, "open", Qt.ConnectionType.QueuedConnection)
            self._open = True

    def close(self) -> None:
        if self._open and self._thread.isRunning():
            QMetaObject.invokeMethod(self._worker, "close", Qt.ConnectionType.BlockingQueuedConnection)
        self._open = False

    def shutdown(self) -> None:
        self.close()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

    @property
    def is_open(self) -> bool:
        return self._open


# --- clips ----------------------------------------------------------------------------


class Clips(QObject):
    """Decoded clips by path, decoded once (again if the file changes)."""

    loaded = Signal(str, bool)  # path, ok

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache: dict[str, tuple[tuple, np.ndarray]] = {}
        self._decoding: dict[str, object] = {}  # path -> QAudioDecoder at work
        self.errors: dict[str, str] = {}

    @staticmethod
    def _stamp(path: Path) -> tuple | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def get(self, path: Path) -> np.ndarray | None:
        """The clip if it's decoded and the file hasn't changed since."""
        hit = self._cache.get(str(path))
        return hit[1] if hit and hit[0] == self._stamp(path) else None

    def load(self, path: Path) -> None:
        """Decode a clip (loaded is emitted when it's ready, or isn't)."""
        key = str(path)
        if key in self._decoding or self.get(path) is not None:
            return
        stamp = self._stamp(path)
        if stamp is None:
            self.errors[key] = "file not found"
            self.loaded.emit(key, False)
            return
        if path.suffix.lower() == ".wav":
            from ..loop.kit import read_wav

            with contextlib.suppress(OSError, ValueError):
                self._done(key, stamp, read_wav(path, RATE))
                return
        self._decode(path, stamp)

    def _decode(self, path: Path, stamp: tuple) -> None:
        from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

        key = str(path)
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        dec = QAudioDecoder(self)
        dec.setAudioFormat(fmt)
        dec.setSource(QUrl.fromLocalFile(key))
        chunks: list[np.ndarray] = []
        limit = MAX_SECONDS * RATE * 2

        def ready() -> None:
            buf = dec.read()
            chunks.append(np.frombuffer(bytes(buf.constData()), np.float32).copy())
            if sum(len(c) for c in chunks) >= limit:
                dec.stop()
                finished()

        def finished() -> None:
            if self._decoding.pop(key, None) is None:
                return
            data = np.concatenate(chunks)[:limit] if chunks else np.zeros(0, np.float32)
            data = data[: len(data) // 2 * 2].reshape(-1, 2)
            if len(data):
                self._done(key, stamp, np.ascontiguousarray(data, np.float32))
            else:
                self.errors[key] = "no audio in it"
                self.loaded.emit(key, False)
            dec.deleteLater()

        def error(_e) -> None:
            if self._decoding.pop(key, None) is None:
                return
            self.errors[key] = dec.errorString() or "can't decode it"
            self.loaded.emit(key, False)
            dec.deleteLater()

        dec.bufferReady.connect(ready)
        dec.finished.connect(finished)
        dec.error.connect(error)
        self._decoding[key] = dec
        dec.start()

    def _done(self, key: str, stamp: tuple, data: np.ndarray) -> None:
        self._cache[key] = (stamp, data)
        self.errors.pop(key, None)
        self.loaded.emit(key, True)

    def keep_only(self, paths: set[str]) -> None:
        """Forget clips no pad uses any more."""
        for key in [k for k in self._cache if k not in paths]:
            del self._cache[key]


# --- the soundboard ------------------------------------------------------------------


class Soundboard(QObject):
    """Plays clips for "Play sound" actions. Create it on the thread with the event loop."""

    changed = Signal()  # what's playing changed (for pad lights)

    def __init__(self, report: Callable[[str], None] = print, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._report = report
        self.clips = Clips(self)
        self.clips.loaded.connect(self._on_loaded)
        self._outputs: dict[str, Output] = {}
        self._waiting: dict[str, list[tuple[str, Settings, float]]] = {}  # path -> presses before it loaded
        self._held: set[str] = set()  # keys of hold-mode pads that are down
        self._idle = QTimer(self, interval=5000, timeout=self._close_idle)
        self._dirty = False
        self._tick = QTimer(self, interval=100, timeout=self._emit_if_dirty)

    # actions

    def press(self, key: str, target: str, options: dict | None = None) -> None:
        s = settings(options)
        path = clip_path(target)
        if not target.strip():
            self._report("Play sound: no file chosen")
            return
        mixer = self._mixer(s.output)
        if s.mode == "hold":
            self._held.add(key)
        elif s.mode == "toggle" and mixer.stop(key):
            self._changed()
            return
        elif s.mode == "restart":
            mixer.stop(key)
        data = self.clips.get(path)
        if data is None:
            self._waiting.setdefault(str(path), []).append((key, s, time.monotonic()))
            self.clips.load(path)
            return
        self._start(key, data, s)

    def release(self, key: str, options: dict | None = None) -> None:
        if settings(options).mode != "hold":
            return
        self._held.discard(key)
        for out in self._outputs.values():
            out.mixer.stop(key)
        self._changed()

    def stop_all(self) -> int:
        self._held.clear()
        self._waiting.clear()
        n = sum(out.mixer.stop() for out in self._outputs.values())
        self._changed()
        return n

    def playing(self) -> set[str]:
        """Keys of the pads whose clips are playing."""
        out: set[str] = set()
        for o in self._outputs.values():
            out |= o.mixer.playing()
        return out

    def preload(self, targets: list[str]) -> None:
        """Decode the clips pads use ahead of their first press; forget the rest."""
        paths = {str(clip_path(t)) for t in targets if t.strip()}
        self.clips.keep_only(paths)
        for p in sorted(paths):
            self.clips.load(Path(p))

    def shutdown(self) -> None:
        self._idle.stop()
        self._tick.stop()
        for out in self._outputs.values():
            out.shutdown()
        self._outputs.clear()

    # inside

    def _mixer(self, device: str) -> Mixer:
        out = self._outputs.get(device)
        if out is None:
            mixer = Mixer()
            mixer.ended = self._mark_dirty
            out = Output(device, mixer, self)
            out.failed.connect(lambda why, d=device: self._report(f"Play sound: {d or 'default output'}: {why}"))
            self._outputs[device] = out
        return out.mixer

    def _start(self, key: str, data: np.ndarray, s: Settings) -> None:
        out = self._outputs[s.output]
        out.mixer.add(Voice(key, data, s.gain))
        out.ensure_open()
        if not self._idle.isActive():
            self._idle.start()
        self._changed()

    def _on_loaded(self, path: str, ok: bool) -> None:
        waiting = self._waiting.pop(path, [])
        if not ok:
            if waiting:
                self._report(f"Play sound: {Path(path).name}: {self.clips.errors.get(path, 'can not play it')}")
            return
        data = self.clips.get(Path(path))
        now = time.monotonic()
        for key, s, when in waiting:
            if data is None or now - when > 3:
                continue  # it took too long: playing it now would be a surprise
            if s.mode == "hold" and key not in self._held:
                continue  # let go before it was ready
            self._start(key, data, s)

    def _mark_dirty(self) -> None:  # audio thread: just a flag, picked up by _tick
        self._dirty = True

    def _emit_if_dirty(self) -> None:
        if self._dirty:
            self._dirty = False
            self.changed.emit()
        if not any(o.mixer.voices for o in self._outputs.values()):
            self._tick.stop()

    def _changed(self) -> None:
        self.changed.emit()
        if not self._tick.isActive():
            self._tick.start()

    def _close_idle(self) -> None:
        now = time.monotonic()
        busy = False
        for out in self._outputs.values():
            if not out.is_open:
                continue
            with out.mixer.lock:
                quiet = not out.mixer.voices and now - out.mixer.quiet_since > IDLE_CLOSE_S
            if quiet:
                out.close()
            else:
                busy = True
        if not busy:
            self._idle.stop()


def output_devices() -> list[str]:
    """The audio outputs' descriptions, as the "output" option names them."""
    from PySide6.QtMultimedia import QMediaDevices

    return [d.description() for d in QMediaDevices.audioOutputs()]
