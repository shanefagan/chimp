"""QML-facing controller: four editable programs mirrored against the device."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

from . import protocol as p
from .device import Lpd8Device

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(n: int) -> str:
    # Akai convention: note 36 = C1 (middle C 60 = C3)
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 2}"


def to_hex_color(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def from_hex_color(text: str) -> tuple[int, int, int]:
    text = text.lstrip("#")
    if len(text) == 8:  # QML may hand us #aarrggbb
        text = text[2:]
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


class Backend(QObject):
    revisionChanged = Signal()
    connectionChanged = Signal()
    busyChanged = Signal()
    currentSlotChanged = Signal()
    activeProgramChanged = Signal()
    previewChanged = Signal()
    notify = Signal(str, str)  # message, kind: info | positive | warning | error
    padHit = Signal(int, int)  # pad index 0-7, velocity (0 = released)
    knobMoved = Signal(int, int)  # knob index 0-7, value

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._working: list[p.Program] = [p.Program() for _ in range(p.NUM_PROGRAMS)]
        self._device_copy: list[p.Program | None] = [None] * p.NUM_PROGRAMS
        self._slot = 1
        self._revision = 0
        self._busy_slots: set[int] = set()
        self._verify_slots: set[int] = set()
        self._auto_read = False
        self._batch_label = ""
        self._active = 0
        self._preview = False
        self._knob_values = [0] * p.NUM_KNOBS

        self._dev = Lpd8Device(self)
        self._dev.connectionChanged.connect(self._on_connection)
        self._dev.programReceived.connect(self._on_program)
        self._dev.activeProgramReceived.connect(self._on_active)
        self._dev.requestTimedOut.connect(self._on_timeout)
        self._dev.noteEvent.connect(self._on_note)
        self._dev.ccEvent.connect(self._on_cc)
        self._dev.pcEvent.connect(self._on_pc)

    def start(self) -> None:
        self._dev.start()

    def shutdown(self) -> None:
        if self._preview:
            self._restore_leds()
        self._dev.stop()

    # --- helpers ------------------------------------------------------------

    @property
    def _prog(self) -> p.Program:
        return self._working[self._slot - 1]

    def _bump(self) -> None:
        self._revision += 1
        self.revisionChanged.emit()
        if self._preview:
            self._push_preview()

    def _set_busy(self, slot: int, busy: bool) -> None:
        (self._busy_slots.add if busy else self._busy_slots.discard)(slot)
        self.busyChanged.emit()

    def _matches_current(self, channel: int, value: int, attr: str) -> list[int]:
        prog = self._prog
        hits = []
        for i, pad in enumerate(prog.pads):
            pad_ch = prog.global_channel if pad.channel == p.GLOBAL_CHANNEL else pad.channel
            if pad_ch == channel and getattr(pad, attr) == value:
                hits.append(i)
        return hits

    # --- properties ---------------------------------------------------------

    @Property(int, notify=revisionChanged)
    def revision(self) -> int:
        return self._revision

    @Property(bool, notify=connectionChanged)
    def connected(self) -> bool:
        return self._dev.connected

    @Property(str, notify=connectionChanged)
    def portName(self) -> str:
        return self._dev.port_name

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return bool(self._busy_slots)

    @Property(int, notify=activeProgramChanged)
    def activeProgram(self) -> int:
        return self._active

    def _get_slot(self) -> int:
        return self._slot

    def _set_slot(self, slot: int) -> None:
        if 1 <= slot <= p.NUM_PROGRAMS and slot != self._slot:
            self._slot = slot
            self.currentSlotChanged.emit()
            self._bump()

    currentSlot = Property(int, _get_slot, _set_slot, notify=currentSlotChanged)

    def _get_preview(self) -> bool:
        return self._preview

    def _set_preview(self, on: bool) -> None:
        if on == self._preview:
            return
        self._preview = on
        self.previewChanged.emit()
        if on:
            self._push_preview()
        else:
            self._restore_leds()

    previewLeds = Property(bool, _get_preview, _set_preview, notify=previewChanged)

    # --- read access for QML --------------------------------------------------

    @Slot(int, result="QVariantMap")
    def pad(self, i: int) -> dict:
        pad = self._prog.pads[i]
        return {
            "note": pad.note,
            "noteName": note_name(pad.note),
            "cc": pad.cc,
            "pc": pad.pc,
            "channel": pad.channel,
            "off": to_hex_color(pad.off_color),
            "on": to_hex_color(pad.on_color),
        }

    @Slot(int, result="QVariantMap")
    def knob(self, i: int) -> dict:
        k = self._prog.knobs[i]
        return {"cc": k.cc, "channel": k.channel, "min": k.min, "max": k.max}

    @Slot(result="QVariantMap")
    def settings(self) -> dict:
        prog = self._prog
        return {
            "globalChannel": prog.global_channel,
            "pressure": p.PRESSURE_MODES.index(prog.pressure),
            "fullLevel": prog.full_level,
            "toggle": prog.toggle,
        }

    @Slot(int, result=bool)
    def isDirty(self, slot: int) -> bool:
        dev = self._device_copy[slot - 1]
        return dev is not None and dev != self._working[slot - 1]

    @Slot(int, result=bool)
    def isKnown(self, slot: int) -> bool:
        return self._device_copy[slot - 1] is not None

    @Slot(int, result=str)
    def noteName(self, n: int) -> str:
        return note_name(n)

    # --- edits ----------------------------------------------------------------

    @Slot(int, str, "QVariant")
    def setPad(self, i: int, field: str, value) -> None:
        pad = self._prog.pads[i]
        if field in ("off", "on"):
            setattr(pad, f"{field}_color", from_hex_color(str(value)))
        elif field in ("note", "cc", "pc", "channel"):
            setattr(pad, field, int(value))
        else:
            return
        self._bump()

    @Slot(int, str, int)
    def setKnob(self, i: int, field: str, value: int) -> None:
        knob = self._prog.knobs[i]
        if field not in ("cc", "channel", "min", "max"):
            return
        setattr(knob, field, int(value))
        self._bump()

    @Slot(str, "QVariant")
    def setSetting(self, field: str, value) -> None:
        prog = self._prog
        if field == "globalChannel":
            prog.global_channel = int(value)
        elif field == "pressure":
            prog.pressure = p.PRESSURE_MODES[int(value)]
        elif field == "fullLevel":
            prog.full_level = bool(value)
        elif field == "toggle":
            prog.toggle = bool(value)
        else:
            return
        self._bump()

    @Slot(str, str)
    def setAllPadColors(self, which: str, color: str) -> None:
        rgb = from_hex_color(color)
        for pad in self._prog.pads:
            setattr(pad, f"{which}_color", rgb)
        self._bump()

    @Slot(str, int)
    def setAllPadChannels(self, _unused: str, channel: int) -> None:
        for pad in self._prog.pads:
            pad.channel = int(channel)
        self._bump()

    @Slot(int)
    def fillSequential(self, start_note: int) -> None:
        """Pads get consecutive notes from start_note (useful for chromatic layouts)."""
        for i, pad in enumerate(self._prog.pads):
            pad.note = min(127, start_note + i)
        self._bump()

    @Slot()
    def resetToFactory(self) -> None:
        self._working[self._slot - 1] = p.Program()
        self._bump()
        self.notify.emit(f"Program {self._slot} reset to factory defaults (not sent yet)", "info")

    @Slot()
    def revert(self) -> None:
        dev = self._device_copy[self._slot - 1]
        if dev is None:
            return
        self._working[self._slot - 1] = dev.copy()
        self._bump()

    # --- device actions ------------------------------------------------------

    @Slot()
    def getCurrent(self) -> None:
        self._batch_label = f"program {self._slot}"
        self._request(self._slot)

    @Slot()
    def getAll(self) -> None:
        self._batch_label = "all 4 programs"
        for slot in range(1, p.NUM_PROGRAMS + 1):
            self._request(slot)

    def _request(self, slot: int) -> None:
        if not self._dev.connected:
            self.notify.emit("LPD8 mk2 not connected", "warning")
            return
        if self._dev.request_program(slot):
            self._set_busy(slot, True)

    @Slot()
    def sendCurrent(self) -> None:
        self._send_slot(self._slot)

    def writeProgram(self, slot: int, prog: p.Program) -> None:
        """Replace a slot's working copy, show it, and send it to the device."""
        self._working[slot - 1] = prog.copy()
        if slot != self._slot:
            self._slot = slot
            self.currentSlotChanged.emit()
        self._bump()
        self._send_slot(slot)

    def _send_slot(self, slot: int) -> None:
        if not self._dev.connected:
            self.notify.emit("LPD8 mk2 not connected", "warning")
            return
        try:
            ok = self._dev.send_program(self._working[slot - 1], slot)
        except p.ProtocolError as e:
            self.notify.emit(str(e), "error")
            return
        if not ok:
            self.notify.emit("Send failed: the device went away", "error")
            return
        self._verify_slots.add(slot)
        self._set_busy(slot, True)
        # The device queue waits for the flash write before reading back to verify.
        self._dev.request_program(slot)
        self._dev.request_active_program()

    @Slot()
    def refreshActive(self) -> None:
        self._dev.request_active_program()

    # --- files ---------------------------------------------------------------

    @Slot(QUrl)
    def loadPreset(self, url: QUrl) -> None:
        path = Path(url.toLocalFile())
        try:
            if path.suffix.lower() == ".syx":
                _, prog = p.parse_program(list(path.read_bytes()))
            else:
                prog = p.Program.from_json_dict(json.loads(path.read_text()))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            self.notify.emit(f"Couldn't load {path.name}: {e}", "error")
            return
        self._working[self._slot - 1] = prog
        self._bump()
        self.notify.emit(f"Loaded {path.name} into program {self._slot} (not sent yet)", "info")

    @Slot(QUrl)
    def savePreset(self, url: QUrl) -> None:
        path = Path(url.toLocalFile())
        try:
            if path.suffix.lower() == ".syx":
                path.write_bytes(bytes(p.send_program(self._prog, self._slot)))
            else:
                if path.suffix.lower() != ".json":
                    path = path.with_suffix(".json")
                path.write_text(json.dumps(self._prog.to_json_dict(), indent=2) + "\n")
        except (OSError, p.ProtocolError) as e:
            self.notify.emit(f"Couldn't save: {e}", "error")
            return
        self.notify.emit(f"Saved {path.name}", "positive")

    @Slot(result=QUrl)
    def presetFolder(self) -> QUrl:
        from .desktop.config import DATA_DIR

        folder = DATA_DIR / "presets"
        folder.mkdir(parents=True, exist_ok=True)
        return QUrl.fromLocalFile(str(folder))

    # --- LED preview ---------------------------------------------------------

    def _push_preview(self) -> None:
        if self._dev.connected:
            self._dev.set_leds([pad.off_color for pad in self._prog.pads])

    def _restore_leds(self) -> None:
        if not self._dev.connected:
            return
        active = self._device_copy[self._active - 1] if self._active else None
        if active is not None:
            self._dev.set_leds([pad.off_color for pad in active.pads])

    # --- device callbacks ----------------------------------------------------

    def _on_connection(self, connected: bool, name: str) -> None:
        self.connectionChanged.emit()
        if connected:
            self.getAll()
            self._auto_read = True
            self._dev.request_active_program()
            self.notify.emit(f"Connected to {name}", "positive")
        else:
            self._busy_slots.clear()
            self._verify_slots.clear()
            self._active = 0
            self.activeProgramChanged.emit()
            self.busyChanged.emit()
            self.notify.emit("LPD8 mk2 disconnected", "warning")

    def _on_program(self, slot: int, prog: p.Program) -> None:
        idx = slot - 1
        verifying = slot in self._verify_slots
        self._verify_slots.discard(slot)
        self._set_busy(slot, False)
        if verifying:
            if prog == self._working[idx]:
                self._device_copy[idx] = prog
                hint = (
                    "switch to another program and back on the LPD8 to load it"
                    if self._active == slot
                    else f"hold PROGRAM and press pad {slot} on the LPD8 to use it"
                )
                self.notify.emit(f"Program {slot} written and verified. {hint[0].upper()}{hint[1:]}.", "positive")
            else:
                self._device_copy[idx] = prog
                self.notify.emit(f"Program {slot} read back differently from what was sent", "error")
            self._bump()
            return
        was_dirty = self.isDirty(slot)
        self._device_copy[idx] = prog
        if not (self._auto_read and was_dirty):
            self._working[idx] = prog.copy()
        self._bump()
        if not self._busy_slots:
            if not self._auto_read and self._batch_label:
                self.notify.emit(f"Read {self._batch_label} from the LPD8", "info")
            self._auto_read = False
            self._batch_label = ""

    def _on_active(self, slot: int) -> None:
        self._active = slot
        self.activeProgramChanged.emit()

    def _on_timeout(self, what: str) -> None:
        if what.startswith("program "):
            slot = int(what.split()[1])
            self._verify_slots.discard(slot)
            self._set_busy(slot, False)
        if what != "active program":
            self.notify.emit(f"No reply from the LPD8 for {what}", "error")

    def _on_note(self, channel: int, note: int, velocity: int) -> None:
        for i in self._matches_current(channel, note, "note"):
            self.padHit.emit(i, velocity)

    def _on_cc(self, channel: int, controller: int, value: int) -> None:
        prog = self._prog
        for i, k in enumerate(prog.knobs):
            k_ch = prog.global_channel if k.channel == p.GLOBAL_CHANNEL else k.channel
            if k_ch == channel and k.cc == controller:
                self._knob_values[i] = value
                self.knobMoved.emit(i, value)
        for i in self._matches_current(channel, controller, "cc"):
            self.padHit.emit(i, value)

    def _on_pc(self, channel: int, program: int) -> None:
        for i in self._matches_current(channel, program, "pc"):
            self.padHit.emit(i, 127)
            QTimer.singleShot(150, lambda i=i: self.padHit.emit(i, 0))
