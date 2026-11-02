"""SysEx protocol for the Akai LPD8 mk2.

Wire format reverse-engineered from the official editor by
stephensrmmartin/lpd8mk2 and extended by uermel/padbound; the read
commands were confirmed against real hardware (firmware Ver00.10).

All messages: F0 47 7F 4C <cmd> ... F7
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

SYSEX_START = 0xF0
SYSEX_END = 0xF7
HEADER = (0x47, 0x7F, 0x4C)  # Akai, all devices, LPD8 mk2

CMD_SEND_PROGRAM = 0x01
CMD_GET_PROGRAM = 0x03
CMD_ACTIVE_PROGRAM = 0x04
CMD_GET_LEDS = 0x05
CMD_SET_LEDS = 0x06
CMD_DEVICE_INFO = 0x5A

PROGRAM_LEN_BYTES = (0x01, 0x29)  # constant length marker used by send + get reply

NUM_PADS = 8
NUM_KNOBS = 8
NUM_PROGRAMS = 4

GLOBAL_CHANNEL = 17  # in the UI/JSON model channels are 1-16, 17 = "use global"
PRESSURE_MODES = ("off", "channel", "polyphonic")

RGB = tuple[int, int, int]


class ProtocolError(ValueError):
    pass


def _check(name: str, value: int, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise ProtocolError(f"{name} must be an integer {lo}-{hi}, got {value!r}")
    return value


def _check_rgb(name: str, rgb: Sequence[int]) -> RGB:
    if len(rgb) != 3:
        raise ProtocolError(f"{name} must have 3 components, got {rgb!r}")
    return tuple(_check(f"{name}[{i}]", int(c), 0, 255) for i, c in enumerate(rgb))  # type: ignore[return-value]


def _split(v: int) -> list[int]:
    return [(v >> 7) & 0x7F, v & 0x7F]


def _join(hi: int, lo: int) -> int:
    return ((hi & 0x7F) << 7) | (lo & 0x7F)


@dataclass
class Pad:
    note: int = 36
    cc: int = 12
    pc: int = 0
    channel: int = 10  # 1-16, 17 = global
    off_color: RGB = (255, 0, 0)
    on_color: RGB = (0, 0, 255)

    def validate(self, i: int = 0) -> None:
        _check(f"pad {i} note", self.note, 0, 127)
        _check(f"pad {i} cc", self.cc, 0, 127)
        _check(f"pad {i} program change", self.pc, 0, 127)
        _check(f"pad {i} channel", self.channel, 1, GLOBAL_CHANNEL)
        self.off_color = _check_rgb(f"pad {i} off colour", self.off_color)
        self.on_color = _check_rgb(f"pad {i} on colour", self.on_color)


@dataclass
class Knob:
    cc: int = 70
    channel: int = GLOBAL_CHANNEL
    min: int = 0
    max: int = 127

    def validate(self, i: int = 0) -> None:
        _check(f"knob {i} cc", self.cc, 0, 127)
        _check(f"knob {i} channel", self.channel, 1, GLOBAL_CHANNEL)
        _check(f"knob {i} min", self.min, 0, 127)
        _check(f"knob {i} max", self.max, 0, 127)


def _default_pads() -> list[Pad]:
    return [Pad(note=36 + i, cc=12 + i, pc=i) for i in range(NUM_PADS)]


def _default_knobs() -> list[Knob]:
    return [Knob(cc=70 + i) for i in range(NUM_KNOBS)]


@dataclass
class Program:
    global_channel: int = 1  # 1-16
    pressure: str = "off"
    full_level: bool = False
    toggle: bool = False
    pads: list[Pad] = field(default_factory=_default_pads)
    knobs: list[Knob] = field(default_factory=_default_knobs)

    def copy(self) -> Program:
        return copy.deepcopy(self)

    def validate(self) -> Program:
        _check("global channel", self.global_channel, 1, 16)
        if self.pressure not in PRESSURE_MODES:
            raise ProtocolError(f"pressure must be one of {PRESSURE_MODES}, got {self.pressure!r}")
        if len(self.pads) != NUM_PADS or len(self.knobs) != NUM_KNOBS:
            raise ProtocolError("a program needs exactly 8 pads and 8 knobs")
        for i, p in enumerate(self.pads, 1):
            p.validate(i)
        for i, k in enumerate(self.knobs, 1):
            k.validate(i)
        return self

    # --- wire format -------------------------------------------------------

    def data_bytes(self) -> list[int]:
        """The program body that follows the program-number byte."""
        self.validate()
        out = [
            self.global_channel - 1,
            PRESSURE_MODES.index(self.pressure),
            0 if self.full_level else 1,  # inverted on the wire
            1 if self.toggle else 0,
        ]
        for p in self.pads:
            out += [p.note, p.cc, p.pc, p.channel - 1]
            for c in p.off_color:
                out += _split(c)
            for c in p.on_color:
                out += _split(c)
        for k in self.knobs:
            out += [k.cc, k.channel - 1, k.min, k.max]
        return out

    @classmethod
    def from_data_bytes(cls, data: Sequence[int]) -> Program:
        expected = 4 + NUM_PADS * 16 + NUM_KNOBS * 4
        if len(data) != expected:
            raise ProtocolError(f"program body must be {expected} bytes, got {len(data)}")
        pressure_idx = data[1]
        prog = cls(
            global_channel=data[0] + 1,
            pressure=PRESSURE_MODES[pressure_idx] if pressure_idx < len(PRESSURE_MODES) else "off",
            full_level=data[2] == 0,
            toggle=data[3] == 1,
            pads=[],
            knobs=[],
        )
        pos = 4
        for _ in range(NUM_PADS):
            b = data[pos : pos + 16]
            colors = [_join(b[j], b[j + 1]) for j in range(4, 16, 2)]
            prog.pads.append(
                Pad(
                    note=b[0],
                    cc=b[1],
                    pc=b[2],
                    channel=b[3] + 1,
                    off_color=tuple(colors[0:3]),  # type: ignore[arg-type]
                    on_color=tuple(colors[3:6]),  # type: ignore[arg-type]
                )
            )
            pos += 16
        for _ in range(NUM_KNOBS):
            b = data[pos : pos + 4]
            prog.knobs.append(Knob(cc=b[0], channel=b[1] + 1, min=b[2], max=b[3]))
            pos += 4
        return prog.validate()

    # --- JSON preset format (compatible with stephensrmmartin/lpd8mk2) -----

    def to_json_dict(self) -> dict:
        return {
            "global_channel": self.global_channel,
            "pressure_message": self.pressure,
            "full_level": self.full_level,
            "toggle": self.toggle,
            "pad_note": [p.note for p in self.pads],
            "pad_cc": [p.cc for p in self.pads],
            "pad_pcn": [p.pc for p in self.pads],
            "pad_channel": [p.channel for p in self.pads],
            "pad_off_color": [list(p.off_color) for p in self.pads],
            "pad_on_color": [list(p.on_color) for p in self.pads],
            "knob_cc": [k.cc for k in self.knobs],
            "knob_channel": [k.channel for k in self.knobs],
            "knob_min": [k.min for k in self.knobs],
            "knob_max": [k.max for k in self.knobs],
        }

    @classmethod
    def from_json_dict(cls, d: dict) -> Program:
        try:
            pads = [
                Pad(note=n, cc=c, pc=pc, channel=ch, off_color=tuple(off), on_color=tuple(on))
                for n, c, pc, ch, off, on in zip(
                    d["pad_note"],
                    d["pad_cc"],
                    d["pad_pcn"],
                    d["pad_channel"],
                    d["pad_off_color"],
                    d["pad_on_color"],
                    strict=True,
                )
            ]
            knobs = [
                Knob(cc=c, channel=ch, min=lo, max=hi)
                for c, ch, lo, hi in zip(d["knob_cc"], d["knob_channel"], d["knob_min"], d["knob_max"], strict=True)
            ]
            prog = cls(
                global_channel=d["global_channel"],
                pressure=str(d["pressure_message"]).lower(),
                full_level=bool(d["full_level"]),
                toggle=bool(d["toggle"]),
                pads=pads,
                knobs=knobs,
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ProtocolError(f"not an LPD8 mk2 preset: {e}") from e
        return prog.validate()


# --- message builders / parsers ---------------------------------------------


def _msg(*body: int) -> list[int]:
    return [SYSEX_START, *HEADER, *body, SYSEX_END]


def _check_slot(slot: int) -> int:
    return _check("program slot", slot, 1, NUM_PROGRAMS)


def request_program(slot: int) -> list[int]:
    return _msg(CMD_GET_PROGRAM, 0x00, 0x01, _check_slot(slot))


def send_program(program: Program, slot: int) -> list[int]:
    return _msg(CMD_SEND_PROGRAM, *PROGRAM_LEN_BYTES, _check_slot(slot), *program.data_bytes())


def request_active_program() -> list[int]:
    return _msg(CMD_ACTIVE_PROGRAM, 0x00, 0x00)


def request_device_info() -> list[int]:
    return _msg(CMD_DEVICE_INFO)


def set_leds(colors: Iterable[Sequence[int]]) -> list[int]:
    """Live pad LED colours (not stored; the next program switch resets them)."""
    colors = list(colors)
    if len(colors) != NUM_PADS:
        raise ProtocolError("need 8 colours")
    body: list[int] = []
    for i, c in enumerate(colors, 1):
        for comp in _check_rgb(f"led {i}", c):
            body += _split(comp)
    return _msg(CMD_SET_LEDS, 0x00, 0x30, *body)


def is_lpd8_sysex(msg: Sequence[int]) -> bool:
    return len(msg) >= 6 and msg[0] == SYSEX_START and tuple(msg[1:4]) == HEADER and msg[-1] == SYSEX_END


def command_of(msg: Sequence[int]) -> int | None:
    return msg[4] if is_lpd8_sysex(msg) else None


def parse_program(msg: Sequence[int]) -> tuple[int, Program]:
    """Parse a get-program reply (cmd 03 01 29) or a send-program message (cmd 01 01 29)."""
    if not is_lpd8_sysex(msg):
        raise ProtocolError("not an LPD8 mk2 SysEx message")
    cmd = msg[4]
    if cmd not in (CMD_GET_PROGRAM, CMD_SEND_PROGRAM) or tuple(msg[5:7]) != PROGRAM_LEN_BYTES:
        raise ProtocolError(f"not a program message (cmd {cmd:#04x})")
    slot = msg[7]
    return slot, Program.from_data_bytes(msg[8:-1])


def parse_active_program(msg: Sequence[int]) -> int:
    if command_of(msg) != CMD_ACTIVE_PROGRAM or len(msg) < 9:
        raise ProtocolError("not an active-program reply")
    return msg[7]


def to_hex(msg: Sequence[int]) -> str:
    return " ".join(f"{b:02X}" for b in msg)


def from_hex(text: str) -> list[int]:
    return [int(t, 16) for t in text.split()]
