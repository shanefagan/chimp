"""Gamepads as controllers: sticks and triggers turn parameters, buttons do anything a
pad or button can. A Wooting analog keyboard in gamepad mode shows up here too (its keys
mapped to sticks and triggers in Wootility, so key depth becomes a knob).

Reads evdev devices (python-evdev; optional: without it there are no gamepads) on one
thread, hotplug-aware. Axes are 0-1 (sticks rest at 0.5, which is "off" for the DJ filter
and "no change" for pitch: spring-loaded modulation). Calls `realtime(device, kind, key,
value)` on its thread at once, and emits `inputEvent` for the Qt thread.
"""

from __future__ import annotations

import contextlib
import selectors
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

try:
    import evdev
    from evdev import ecodes
except ImportError:  # optional
    evdev = None
    ecodes = None

RESCAN_S = 2.0
MIN_STEP = 1 / 256  # ignore jitter smaller than this


def _is_gamepad(dev) -> bool:
    caps = dev.capabilities()
    keys = set(caps.get(ecodes.EV_KEY, []))
    pad_buttons = {ecodes.BTN_SOUTH, ecodes.BTN_GAMEPAD, ecodes.BTN_JOYSTICK, ecodes.BTN_TRIGGER}
    return bool(caps.get(ecodes.EV_ABS)) and bool(keys & pad_buttons)


def _name(table: dict, code: int) -> str:
    n = table.get(code, str(code))
    return n[0] if isinstance(n, list) else n


class Gamepads(QObject):
    # (not `event`: that would hide QObject.event(), which Qt calls for every event)
    inputEvent = Signal(str, str, str, float)  # device, kind ("value"/"press"/"release"), key, value
    devicesChanged = Signal()

    def __init__(self, realtime: Callable[[str, str, str, float], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._realtime = realtime
        self._stop = threading.Event()
        self._devices: dict[str, object] = {}  # path -> InputDevice
        self._ranges: dict[tuple[str, int], tuple[int, int]] = {}
        self._last: dict[tuple[str, int], float] = {}
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return evdev is not None

    @property
    def names(self) -> list[str]:
        return sorted({d.name for d in list(self._devices.values())})

    def start(self) -> None:
        if evdev is None or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="chimp-loop-gamepads", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _scan(self, sel: selectors.BaseSelector) -> bool:
        changed = False
        paths = set(evdev.list_devices())
        for path in list(self._devices):
            if path not in paths:
                with contextlib.suppress(Exception):
                    sel.unregister(self._devices[path])
                    self._devices[path].close()
                del self._devices[path]
                changed = True
        for path in paths - set(self._devices):
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue  # no access: not ours to read
            if not _is_gamepad(dev):
                dev.close()
                continue
            for code, info in dev.capabilities(absinfo=True).get(ecodes.EV_ABS, []):
                self._ranges[(path, code)] = (info.min, info.max)
            self._devices[path] = dev
            sel.register(dev, selectors.EVENT_READ, path)
            changed = True
        return changed

    def _run(self) -> None:
        sel = selectors.DefaultSelector()
        next_scan = 0.0
        while not self._stop.is_set():
            if time.monotonic() >= next_scan:
                if self._scan(sel):
                    self.devicesChanged.emit()
                next_scan = time.monotonic() + RESCAN_S
            if not self._devices:
                self._stop.wait(RESCAN_S)
                continue
            for key, _mask in sel.select(timeout=0.5):
                path = key.data
                dev = self._devices.get(path)
                if dev is None:
                    continue
                try:
                    events = list(dev.read())
                except OSError:  # unplugged: the next scan drops it
                    with contextlib.suppress(Exception):
                        sel.unregister(dev)
                    self._devices.pop(path, None)
                    self.devicesChanged.emit()
                    continue
                for ev in events:
                    self._handle(path, dev.name, ev)

    def _handle(self, path: str, name: str, ev) -> None:
        if ev.type == ecodes.EV_ABS:
            lo, hi = self._ranges.get((path, ev.code), (0, 255))
            v = (ev.value - lo) / (hi - lo) if hi > lo else 0.0
            last = self._last.get((path, ev.code))
            if last is not None and abs(v - last) < MIN_STEP:
                return
            self._last[(path, ev.code)] = v
            self._fire(name, "value", "gpa:" + _name(ecodes.ABS, ev.code), v)
        elif ev.type == ecodes.EV_KEY and ev.value in (0, 1):
            key = "gpb:" + _name(ecodes.BTN, ev.code) if ev.code in ecodes.BTN else "gpb:" + _name(ecodes.KEY, ev.code)
            self._fire(name, "press" if ev.value else "release", key, 1.0 if ev.value else 0.0)

    def _fire(self, device: str, kind: str, key: str, value: float) -> None:
        with contextlib.suppress(Exception):  # never let an error kill the reader
            self._realtime(device, kind, key, value)
        self.inputEvent.emit(device, kind, key, value)
