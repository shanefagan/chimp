"""ALSA sequencer I/O for the LPD8 mk2, via python-rtmidi.

Why the sequencer and not raw MIDI: with PipeWire running, the kernel's
sequencer client holds the raw MIDI input open, so `hw:X,0,0` reads fail
with EBUSY. rtmidi creates its own sequencer ports and *subscribes* them to
the device, which is also what makes the kernel open the device's output
(direct-addressed events without a subscription are silently dropped).

The LPD8 only handles one SysEx request at a time: fire four "get program"
requests back-to-back and only the first is answered. So every outgoing
SysEx goes through a queue with at most one message in flight; the next one
is sent when the reply arrives (or times out), or after a short settle gap
for messages that get no reply (send program, set LEDs).
"""

from __future__ import annotations

import contextlib
import time
from collections import deque

import rtmidi
from PySide6.QtCore import QObject, QTimer, Signal

from . import protocol as p

DEVICE_CLIENT = "lpd8 mk2:"  # rtmidi port names look like "LPD8 mk2:LPD8 mk2 MIDI 1 28:0"
CLIENT_NAME = "Chimp"
REPLY_TIMEOUT_S = 1.5
SEND_PROGRAM_GAP_MS = 250  # time for the device to write flash before the next request
SET_LEDS_GAP_MS = 20
GAP = ("gap",)


class Lpd8Device(QObject):
    """Owns the MIDI ports. Lives on the GUI thread; input is drained by a timer."""

    connectionChanged = Signal(bool, str)  # connected, port name
    programReceived = Signal(int, object)  # slot, protocol.Program
    activeProgramReceived = Signal(int)
    requestTimedOut = Signal(str)  # human-readable description
    noteEvent = Signal(int, int, int)  # channel (1-16), note, velocity (0 = off)
    ccEvent = Signal(int, int, int)  # channel, controller, value
    pcEvent = Signal(int, int)  # channel, program

    def __init__(self, parent: QObject | None = None, client_name: str = CLIENT_NAME) -> None:
        super().__init__(parent)
        self._client_name = client_name
        self._probe = rtmidi.MidiIn(rtmidi.API_LINUX_ALSA, name=f"{client_name} (probe)")
        self._in: rtmidi.MidiIn | None = None
        self._out: rtmidi.MidiOut | None = None
        self._port_name = ""

        # Outgoing queue entries: (message, reply key or None, description, gap ms if no reply, retries left)
        self._queue: deque[tuple[list[int], tuple | None, str, int, int]] = deque()
        self._inflight_entry: tuple | None = None
        self._inflight: tuple | None = None  # reply key we're waiting for, or GAP
        self._inflight_desc = ""
        self._deadline = 0.0

        self._scan_timer = QTimer(self, interval=1500, timeout=self._scan)
        self._drain_timer = QTimer(self, interval=10, timeout=self._drain)
        self._gap_timer = QTimer(self, singleShot=True, timeout=self._gap_done)

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._scan()
        self._scan_timer.start()

    def stop(self) -> None:
        """Close the ports. Queued fire-and-forget messages (e.g. an LED restore) still go out."""
        self._scan_timer.stop()
        if self._out is not None:
            for msg, key, _desc, gap_ms, _retries in self._queue:
                if key is None:
                    try:
                        self._out.send_message(msg)
                    except rtmidi.SystemError:
                        break
                    time.sleep(gap_ms / 1000)
        self._close()

    @property
    def connected(self) -> bool:
        return self._out is not None

    @property
    def port_name(self) -> str:
        return self._port_name

    @staticmethod
    def _find(ports: list[str]) -> int | None:
        for i, name in enumerate(ports):
            if name.lower().startswith(DEVICE_CLIENT):
                return i
        return None

    def other_clients(self) -> set[str]:
        """Names of other sequencer clients currently visible (e.g. to spot a running editor)."""
        try:
            ports = self._probe.get_ports()
        except rtmidi.SystemError:
            return set()
        return {name.split(":", 1)[0] for name in ports}

    def _scan(self) -> None:
        try:
            present = self._find(self._probe.get_ports()) is not None
        except rtmidi.SystemError:
            present = False
        if present and not self.connected:
            self._open()
        elif not present and self.connected:
            self._close()

    def _open(self) -> None:
        try:
            midi_in = rtmidi.MidiIn(rtmidi.API_LINUX_ALSA, name=self._client_name, queue_size_limit=4096)
            midi_out = rtmidi.MidiOut(rtmidi.API_LINUX_ALSA, name=self._client_name)
            in_idx = self._find(midi_in.get_ports())
            out_idx = self._find(midi_out.get_ports())
            if in_idx is None or out_idx is None:
                return
            midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
            midi_in.open_port(in_idx, "from LPD8")
            midi_out.open_port(out_idx, "to LPD8")
            self._port_name = midi_out.get_ports()[out_idx].rsplit(" ", 1)[0]
        except (rtmidi.SystemError, rtmidi.InvalidPortError):
            return
        self._in, self._out = midi_in, midi_out
        self._reset_queue()
        self._drain_timer.start()
        self.connectionChanged.emit(True, self._port_name)

    def _close(self) -> None:
        self._drain_timer.stop()
        for port in (self._in, self._out):
            if port is not None:
                with contextlib.suppress(rtmidi.RtMidiError, RuntimeError):
                    port.close_port()
                    port.delete()
        was_connected = self.connected
        self._in = self._out = None
        self._port_name = ""
        self._reset_queue()
        if was_connected:
            self.connectionChanged.emit(False, "")

    def _reset_queue(self) -> None:
        self._queue.clear()
        self._inflight = None
        self._inflight_entry = None
        self._gap_timer.stop()

    # --- outgoing (one SysEx in flight at a time) ---------------------------

    def _enqueue(self, msg: list[int], key: tuple | None, description: str, gap_ms: int = 0) -> bool:
        if self._out is None:
            return False
        if key is not None and (key == self._inflight or any(q[1] == key for q in self._queue)):
            return True  # the same request is already pending
        # Requests get one retry: another client (editor or desktop service) may have
        # been talking to the device at the same moment, and it drops overlapping requests.
        self._queue.append((msg, key, description, gap_ms, 1 if key is not None else 0))
        self._pump()
        return True

    def _pump(self) -> None:
        while self._inflight is None and self._queue and self._out is not None:
            entry = self._queue.popleft()
            msg, key, description, gap_ms, _retries = entry
            try:
                self._out.send_message(msg)
            except rtmidi.SystemError:
                self._close()
                return
            if key is not None:
                self._inflight = key
                self._inflight_entry = entry
                self._inflight_desc = description
                self._deadline = time.monotonic() + REPLY_TIMEOUT_S
            elif gap_ms > 0:
                self._inflight = GAP
                self._gap_timer.start(gap_ms)

    def _gap_done(self) -> None:
        if self._inflight == GAP:
            self._inflight = None
            self._pump()

    def _reply(self, key: tuple) -> None:
        if self._inflight == key:
            self._inflight = None
            self._pump()

    def request_program(self, slot: int) -> bool:
        return self._enqueue(p.request_program(slot), ("program", slot), f"program {slot}")

    def send_program(self, program: p.Program, slot: int) -> bool:
        return self._enqueue(p.send_program(program, slot), None, f"send {slot}", SEND_PROGRAM_GAP_MS)

    def request_active_program(self) -> bool:
        return self._enqueue(p.request_active_program(), ("active",), "active program")

    def set_leds(self, colors: list[tuple[int, int, int]]) -> bool:
        msg = p.set_leds(colors)
        # Only the latest LED frame matters; drop any still waiting.
        self._queue = deque(q for q in self._queue if q[2] != "leds")
        if not self.connected:
            return False
        return self._enqueue(msg, None, "leds", SET_LEDS_GAP_MS)

    # --- incoming -----------------------------------------------------------

    def _drain(self) -> None:
        if self._in is None:
            return
        for _ in range(512):
            try:
                item = self._in.get_message()
            except rtmidi.SystemError:
                self._close()
                return
            if item is None:
                break
            self._dispatch(item[0])
        self._check_timeout()

    def _dispatch(self, msg: list[int]) -> None:
        if not msg:
            return
        status = msg[0]
        if status == p.SYSEX_START:
            cmd = p.command_of(msg)
            if cmd == p.CMD_GET_PROGRAM:
                try:
                    slot, prog = p.parse_program(msg)
                except p.ProtocolError:
                    return
                self._reply(("program", slot))  # clear before emitting so handlers can re-request
                self.programReceived.emit(slot, prog)
            elif cmd == p.CMD_ACTIVE_PROGRAM:
                try:
                    slot = p.parse_active_program(msg)
                except p.ProtocolError:
                    return
                self._reply(("active",))
                self.activeProgramReceived.emit(slot)
            return
        kind, channel = status & 0xF0, (status & 0x0F) + 1
        if kind == 0x90 and len(msg) >= 3:
            self.noteEvent.emit(channel, msg[1], msg[2])
        elif kind == 0x80 and len(msg) >= 3:
            self.noteEvent.emit(channel, msg[1], 0)
        elif kind == 0xB0 and len(msg) >= 3:
            self.ccEvent.emit(channel, msg[1], msg[2])
        elif kind == 0xC0 and len(msg) >= 2:
            self.pcEvent.emit(channel, msg[1])

    def _check_timeout(self) -> None:
        if self._inflight is None or self._inflight == GAP:
            return
        if time.monotonic() > self._deadline:
            entry = self._inflight_entry
            self._inflight = None
            self._inflight_entry = None
            if entry is not None and entry[4] > 0:
                self._queue.appendleft((*entry[:4], entry[4] - 1))
            else:
                self.requestTimedOut.emit(self._inflight_desc)
            self._pump()
