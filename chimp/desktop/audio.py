"""In-process volume/mute control through PipeWire's PulseAudio server (pulsectl).

Spawning `wpctl` for every knob step costs a process each time; with pulsectl a volume
change is one IPC call. pulsectl is optional: without it (or if the connection fails)
callers fall back to wpctl. Connections are per thread because a pulsectl client isn't
thread-safe (the editor reads levels on a worker thread).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

try:
    import pulsectl
except (ImportError, OSError):  # optional; OSError: installed, but libpulse isn't
    pulsectl = None

_local = threading.local()


def available() -> bool:
    return _client() is not None


def _client():
    if pulsectl is None:
        return None
    pulse = getattr(_local, "pulse", None)
    if pulse is not None:
        return pulse
    try:
        pulse = pulsectl.Pulse("chimpd", connect=True)
    except Exception:  # noqa: BLE001 - any failure means "use the fallback"
        return None
    _local.pulse = pulse
    return pulse


def _drop() -> None:
    pulse = getattr(_local, "pulse", None)
    _local.pulse = None
    if pulse is not None:
        with contextlib.suppress(Exception):  # it's already broken; nothing to report
            pulse.close()


def _default(pulse, kind: str):
    info = pulse.server_info()
    if kind == "sink":
        return pulse.get_sink_by_name(info.default_sink_name)
    return pulse.get_source_by_name(info.default_source_name)


def _stream(pulse, node_id: int):
    for s in pulse.sink_input_list():
        if s.proplist.get("object.id") == str(node_id):
            return s
    return None


def _target(pulse, node):
    """node: "sink" / "source" (the defaults) or a PipeWire stream node id."""
    if node in ("sink", "source"):
        return _default(pulse, node)
    return _stream(pulse, int(node))


def _guard(fn: Callable, fallback):
    pulse = _client()
    if pulse is None:
        return fallback
    try:
        return fn(pulse)
    except Exception:  # noqa: BLE001 - lost connection, object vanished: reconnect next time
        _drop()
        return fallback


def set_volume(node, fraction: float) -> bool:
    """True if done; False means the caller should fall back to wpctl."""

    def go(pulse):
        obj = _target(pulse, node)
        if obj is None:
            return False
        pulse.volume_set_all_chans(obj, max(0.0, min(fraction, 1.5)))
        return True

    return _guard(go, False)


def change_volume(node, delta: float, limit: float = 1.0) -> float | None:
    """Add delta (e.g. +0.02) to the volume, capped at limit; returns the new volume."""

    def go(pulse):
        obj = _target(pulse, node)
        if obj is None:
            return None
        new = max(0.0, min(limit, obj.volume.value_flat + delta))
        pulse.volume_set_all_chans(obj, new)
        return new

    return _guard(go, None)


def get_volume(node) -> tuple[float | None, bool] | None:
    """(volume, muted), or None when the caller should fall back."""

    def go(pulse):
        obj = _target(pulse, node)
        if obj is None:
            return (None, False)
        return (obj.volume.value_flat, bool(obj.mute))

    return _guard(go, None)


def toggle_mute(node) -> bool:
    def go(pulse):
        obj = _target(pulse, node)
        if obj is None:
            return False
        pulse.mute(obj, not obj.mute)
        return True

    return _guard(go, False)


class MuteWatcher:
    """Calls `on_change` (from a background thread) whenever a sink or source changes,
    so pad lights follow mutes without polling. Returns False from start() if unavailable."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if pulsectl is None:
            return False
        self._thread = threading.Thread(target=self._run, name="pulse-events", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with pulsectl.Pulse("chimp-events") as pulse:
                    pulse.event_mask_set("sink", "source", "server")

                    def cb(ev):
                        if ev.t == "change" or ev.facility == "server":
                            self._on_change()
                        if self._stop.is_set():
                            raise pulsectl.PulseLoopStop

                    pulse.event_callback_set(cb)
                    while not self._stop.is_set():
                        pulse.event_listen(timeout=1.0)
            except Exception:  # noqa: BLE001 - PipeWire restarting: retry shortly
                self._stop.wait(3.0)
