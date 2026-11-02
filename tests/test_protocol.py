"""Protocol tests against SysEx read back from a real LPD8 mk2 (firmware Ver00.10)."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from chimp import protocol as p

# Replies to F0 47 7F 4C 03 00 01 0N F7, captured with aseqdump on 2026-11-02.
DEVICE_REPLIES = {
    1: "F0 47 7F 4C 03 01 29 01 00 00 01 00 24 0C 00 09 01 7F 00 00 00 00 00 00 00 00 01 7F 25 0D 01 09 01 7F 00 00 00 00 00 00 00 00 01 7F 26 0E 02 09 01 7F 00 00 00 00 00 00 00 00 01 7F 27 0F 03 09 01 7F 00 00 00 00 00 00 00 00 01 7F 28 10 04 09 01 7F 00 00 00 00 00 00 00 00 01 7F 29 11 05 09 01 7F 00 00 00 00 00 00 00 00 01 7F 2A 12 06 09 01 7F 00 00 00 00 00 00 00 00 01 7F 2B 13 07 09 01 7F 00 00 00 00 00 00 00 00 01 7F 46 10 00 7F 47 10 00 7F 48 10 00 7F 49 10 00 7F 4A 10 00 7F 4B 10 00 7F 4C 10 00 7F 4D 10 00 7F F7",
    2: "F0 47 7F 4C 03 01 29 02 00 01 01 00 24 0C 00 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 25 0D 01 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 26 0E 02 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 27 0F 03 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 28 10 04 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 29 11 05 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 2A 12 06 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 2B 13 07 09 00 00 01 7F 00 00 01 7F 00 00 01 7F 46 10 00 7F 47 10 00 7F 48 10 00 7F 49 10 00 7F 4A 10 00 7F 4B 10 00 7F 4C 10 00 7F 4D 10 00 7F F7",
    3: "F0 47 7F 4C 03 01 29 03 00 02 01 00 24 0C 00 09 00 00 01 7F 00 00 01 7F 00 00 00 00 25 0D 01 09 00 00 01 7F 00 00 01 7F 00 00 00 00 26 0E 02 09 00 00 01 7F 00 00 01 7F 00 00 00 00 27 0F 03 09 00 00 01 7F 00 00 01 7F 00 00 00 00 28 10 04 09 00 00 01 7F 00 00 01 7F 00 00 00 00 29 11 05 09 00 00 01 7F 00 00 01 7F 00 00 00 00 2A 12 06 09 00 00 01 7F 00 00 01 7F 00 00 00 00 2B 13 07 09 00 00 01 7F 00 00 01 7F 00 00 00 00 46 10 00 7F 47 10 00 7F 48 10 00 7F 49 10 00 7F 4A 10 00 7F 4B 10 00 7F 4C 10 00 7F 4D 10 00 7F F7",
    4: "F0 47 7F 4C 03 01 29 04 00 00 00 01 24 0C 00 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 25 0D 01 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 26 0E 02 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 27 0F 03 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 28 10 04 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 29 11 05 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 2A 12 06 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 2B 13 07 09 01 7F 00 00 01 7F 00 00 01 7F 00 00 46 10 00 7F 47 10 00 7F 48 10 00 7F 49 10 00 7F 4A 10 00 7F 4B 10 00 7F 4C 10 00 7F 4D 10 00 7F F7",
}

# What the official Windows editor sent for "Send Program 1" (stephensrmmartin capture).
OFFICIAL_SEND_P1 = "F0 47 7F 4C 01 01 29 01 00 00 01 00 24 0C 00 09 01 7F 00 00 00 00 00 00 00 00 01 7F 25 0D 01 09 01 7F 00 00 00 00 00 00 00 00 01 7F 26 0E 02 09 01 7F 00 00 00 00 00 00 00 00 01 7F 27 0F 03 09 01 7F 00 00 00 00 00 00 00 00 01 7F 28 10 04 09 01 7F 00 00 00 00 00 00 00 00 01 7F 29 11 05 09 01 7F 00 00 00 00 00 00 00 00 01 7F 2A 12 06 09 01 7F 00 00 00 00 00 00 00 00 01 7F 2B 13 07 09 01 7F 00 00 00 00 00 00 00 00 01 7F 46 10 00 7F 47 10 00 7F 48 10 00 7F 49 10 00 7F 4A 10 00 7F 4B 10 00 7F 4C 10 00 7F 4D 10 00 7F F7"

RED, GREEN, BLUE, MAGENTA = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 0, 255)


@pytest.mark.parametrize(
    "slot, pressure, full_level, toggle, off, on",
    [
        (1, "off", False, False, RED, BLUE),
        (2, "channel", False, False, GREEN, MAGENTA),
        (3, "polyphonic", False, False, GREEN, RED),
        (4, "off", True, True, MAGENTA, GREEN),
    ],
)
def test_parse_factory_programs(slot, pressure, full_level, toggle, off, on):
    got_slot, prog = p.parse_program(p.from_hex(DEVICE_REPLIES[slot]))
    assert got_slot == slot
    assert prog.global_channel == 1
    assert prog.pressure == pressure
    assert prog.full_level is full_level
    assert prog.toggle is toggle
    assert [pad.note for pad in prog.pads] == list(range(36, 44))
    assert [pad.cc for pad in prog.pads] == list(range(12, 20))
    assert [pad.pc for pad in prog.pads] == list(range(8))
    assert all(pad.channel == 10 for pad in prog.pads)
    assert all(pad.off_color == off and pad.on_color == on for pad in prog.pads)
    assert [k.cc for k in prog.knobs] == list(range(70, 78))
    assert all(k.channel == p.GLOBAL_CHANNEL and k.min == 0 and k.max == 127 for k in prog.knobs)


@pytest.mark.parametrize("slot", [1, 2, 3, 4])
def test_reply_reencodes_byte_for_byte(slot):
    reply = p.from_hex(DEVICE_REPLIES[slot])
    _, prog = p.parse_program(reply)
    sent = p.send_program(prog, slot)
    # A send is the reply with the command byte 0x03 swapped for 0x01.
    assert sent == [*reply[:4], p.CMD_SEND_PROGRAM, *reply[5:]]


def test_matches_official_editor_send():
    _, prog = p.parse_program(p.from_hex(DEVICE_REPLIES[1]))
    assert p.to_hex(p.send_program(prog, 1)) == OFFICIAL_SEND_P1


def test_default_program_is_factory_program_1():
    assert p.send_program(p.Program(), 1) == p.from_hex(OFFICIAL_SEND_P1)


def test_colour_split_round_trip():
    prog = p.Program()
    prog.pads[0].off_color = (0xAD, 0xD8, 0xE6)
    prog.pads[7].on_color = (0xC5, 0xEB, 0x43)
    _, back = p.parse_program(p.send_program(prog, 2))
    assert back == prog
    msg = p.send_program(prog, 2)
    # Values from the official editor capture for #ADD8E6: 01 2D 01 58 01 66
    assert msg[12 + 4 : 12 + 10] == [0x01, 0x2D, 0x01, 0x58, 0x01, 0x66]
    assert all(b < 0x80 for b in msg[1:-1])


def test_json_compatible_with_lpd8mk2_presets():
    prog = p.Program()
    prog.pads[3].channel = p.GLOBAL_CHANNEL
    prog.knobs[2].max = 64
    d = json.loads(json.dumps(prog.to_json_dict()))
    assert p.Program.from_json_dict(d) == prog
    assert set(d) == {
        "global_channel",
        "pressure_message",
        "full_level",
        "toggle",
        "pad_note",
        "pad_cc",
        "pad_pcn",
        "pad_channel",
        "pad_off_color",
        "pad_on_color",
        "knob_cc",
        "knob_channel",
        "knob_min",
        "knob_max",
    }


def test_validation_rejects_out_of_range():
    prog = p.Program()
    prog.pads[0].note = 128
    with pytest.raises(p.ProtocolError):
        p.send_program(prog, 1)
    with pytest.raises(p.ProtocolError):
        p.request_program(5)


def test_small_messages():
    assert p.to_hex(p.request_program(3)) == "F0 47 7F 4C 03 00 01 03 F7"
    assert p.to_hex(p.request_active_program()) == "F0 47 7F 4C 04 00 00 F7"
    assert p.parse_active_program(p.from_hex("F0 47 7F 4C 04 00 01 01 F7")) == 1
    leds = p.set_leds([(255, 255, 255)] * 8)
    assert leds[:7] == [0xF0, 0x47, 0x7F, 0x4C, 0x06, 0x00, 0x30] and len(leds) == 7 + 48 + 1
