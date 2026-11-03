"""Sound out: a QAudioSink in its own thread, pulling blocks from the engine.

Pull mode: the sink asks for data when its buffer has room, so the engine renders
small blocks just in time. The buffer is kept small (a few ms) for low latency;
`latency_frames` is what the engine uses to place recorded hits where they were heard.
"""

from __future__ import annotations

from PySide6.QtCore import QIODevice, QObject, QThread, Signal, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from .engine import Engine

BYTES_PER_FRAME = 8  # float32 stereo
MAX_BLOCK = 256  # frames rendered per pull, at most


class _Source(QIODevice):
    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return MAX_BLOCK * BYTES_PER_FRAME * 4 + super().bytesAvailable()

    def readData(self, maxlen: int):
        n = min(maxlen // BYTES_PER_FRAME, MAX_BLOCK)
        if n <= 0:
            return b""
        return self._engine.render(n).tobytes()

    def writeData(self, _data) -> int:
        return -1


class _Worker(QObject):
    started = Signal(int, str)  # latency frames, device name
    failed = Signal(str)

    def __init__(self, engine: Engine, buffer_frames: int) -> None:
        super().__init__()
        self._engine = engine
        self._buffer_frames = buffer_frames
        self._sink: QAudioSink | None = None
        self._source: _Source | None = None

    @Slot()
    def start(self) -> None:
        device = QMediaDevices.defaultAudioOutput()
        fmt = QAudioFormat()
        fmt.setSampleRate(self._engine.rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        if not device.isFormatSupported(fmt):
            self.failed.emit(f"{device.description()} can't play float stereo at {self._engine.rate} Hz")
            return
        self._sink = QAudioSink(device, fmt)
        self._sink.setBufferSize(self._buffer_frames * BYTES_PER_FRAME)
        self._source = _Source(self._engine)
        self._sink.start(self._source)
        if self._sink.error().name != "NoError":
            self.failed.emit(f"audio output error: {self._sink.error()}")
            return
        frames = max(self._sink.bufferSize() // BYTES_PER_FRAME, self._buffer_frames)
        self.started.emit(frames, device.description())

    @Slot()
    def stop(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        if self._source is not None:
            self._source.close()
            self._source = None


class AudioOut(QObject):
    """Owns the audio thread. started(latency_frames, device) once sound is flowing."""

    started = Signal(int, str)
    failed = Signal(str)

    def __init__(self, engine: Engine, buffer_frames: int = 1024, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._thread = QThread()
        self._thread.setObjectName("chimp-loop-audio")
        self._worker = _Worker(engine, buffer_frames)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.started.connect(self._on_started)
        self._worker.failed.connect(self.failed)

    def _on_started(self, frames: int, device: str) -> None:
        self._engine.latency = frames
        self.started.emit(frames, device)

    def start(self) -> None:
        self._thread.start()
        self._thread.setPriority(QThread.Priority.TimeCriticalPriority)

    def stop(self) -> None:
        if self._thread.isRunning():
            from PySide6.QtCore import QMetaObject, Qt

            QMetaObject.invokeMethod(self._worker, "stop", Qt.ConnectionType.BlockingQueuedConnection)
            self._thread.quit()
            self._thread.wait(2000)
