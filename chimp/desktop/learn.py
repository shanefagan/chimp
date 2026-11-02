"""Work out what a device's controls are from the MIDI they send ("learn" mode).

Move every knob/fader through its range and press every button a couple of times;
`classify` turns the captured messages into controls:

- notes and program changes        -> buttons
- pitch bend                       -> fader (14-bit)
- CC only ever 0/127 (two values)  -> button; momentary if every press is released
                                      quickly, toggle if presses alternate on/off
- CC repeating the same few values
  around 64 (63/65, 62/66, ...)    -> relative encoder, "64 +/- n" style
  near 1 or 127 (1, 2 / 127, 126)  -> relative encoder, two's-complement style
- any other CC                     -> knob (absolute position)
- CC 32-63 that shadow CC 0-31     -> dropped (14-bit LSB of another control), and
                                      the control they belong to is a knob
- aftertouch / pressure            -> ignored
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise

from .config import Control

Event = tuple[float, list[int]]  # (time in seconds, MIDI bytes)


def _address(msg: list[int]) -> tuple[str, int, int, int] | None:
    """(msg type, channel 1-16, number, value) or None for messages we don't learn."""
    if len(msg) == 1 and msg[0] in (0xFA, 0xFB, 0xFC):  # start / continue / stop
        return "rt", 1, msg[0], 127
    if len(msg) < 2:
        return None
    kind, ch = msg[0] & 0xF0, (msg[0] & 0x0F) + 1
    if kind in (0x80, 0x90) and len(msg) >= 3:
        vel = msg[2] if kind == 0x90 else 0
        return "note", ch, msg[1], vel
    if kind == 0xB0 and len(msg) >= 3:
        return "cc", ch, msg[1], msg[2]
    if kind == 0xE0 and len(msg) >= 3:
        return "pb", ch, 0, msg[1] | (msg[2] << 7)
    if kind == 0xC0:
        return "pc", ch, msg[1], 127
    return None


def _button_mode(samples: list[tuple[float, int]]) -> str:
    """momentary if each press (value > 0) is followed by a release (0) within 0.8 s."""
    presses = releases_quick = 0
    for i, (t, v) in enumerate(samples):
        if v > 0:
            presses += 1
            nxt = samples[i + 1] if i + 1 < len(samples) else None
            if nxt and nxt[1] == 0 and nxt[0] - t < 0.8:
                releases_quick += 1
    if presses == 0:
        return "momentary"
    return "momentary" if releases_quick >= presses * 0.75 else "toggle"


def _cc_kind(samples: list[tuple[float, int]]) -> tuple[str, str]:
    values = [v for _, v in samples]
    distinct = set(values)
    if distinct <= {0, 127} or (len(distinct) <= 2 and 0 in distinct and len(values) >= 2):
        return "button", _button_mode(samples)
    # Absolute knobs only send when the position changes, so they rarely repeat a value;
    # encoders send the same "one step left/right" value over and over.
    repeats = sum(1 for a, b in pairwise(values) if a == b)
    repetitive = len(values) >= 4 and repeats / (len(values) - 1) >= 0.3
    if repetitive:
        if all(54 <= v <= 74 for v in values) and Counter(values).most_common(1)[0][0] != 64:
            return "encoder", "rel64"
        if all(v <= 15 or v >= 113 for v in values):
            return "encoder", "rel2c"
    return "knob", "absolute"


def classify(events: list[Event]) -> list[Control]:
    """Controls in the order they were first touched."""
    first_seen: dict[tuple, float] = {}
    samples: dict[tuple, list[tuple[float, int]]] = {}
    for t, msg in events:
        addr = _address(msg)
        if addr is None:
            continue
        msg_type, ch, num, value = addr
        key = (msg_type, ch, num)
        first_seen.setdefault(key, t)
        samples.setdefault(key, []).append((t, value))

    # 14-bit CC pairs: CC n+32 carries the low bits of CC n; keep only the main one, and
    # remember it was 14-bit: that makes it an absolute control even if a small nudge
    # only moved the high byte between 64 and 65, which would look like an encoder.
    fourteen_bit: set[tuple] = set()
    for key in list(samples):
        msg_type, ch, num = key
        if msg_type == "cc" and 32 <= num <= 63 and ("cc", ch, num - 32) in samples:
            del samples[key]
            fourteen_bit.add(("cc", ch, num - 32))

    controls: list[Control] = []
    counters: Counter = Counter()
    for key in sorted(samples, key=lambda k: first_seen[k]):
        msg_type, ch, num = key
        if msg_type in ("note", "pc", "rt"):
            kind, mode = "button", "momentary"
        elif msg_type == "pb":
            kind, mode = "fader", "absolute"
        elif key in fourteen_bit:
            kind, mode = "knob", "absolute"
        else:
            kind, mode = _cc_kind(samples[key])
        counters[kind] += 1
        controls.append(
            Control(
                name=f"{kind.capitalize()} {counters[kind]}", kind=kind, msg=msg_type, channel=ch, number=num, mode=mode
            )
        )
    return controls


# --- turning an incoming message into what the control did ---------------------------


def decode(control: Control, msg: list[int]) -> tuple[str, float | int] | None:
    """('level', 0-1) for knobs/faders, ('step', +/-n) for encoders, ('press', 1) for buttons."""
    addr = _address(msg)
    if addr is None:
        return None
    msg_type, _ch, _num, value = addr
    if control.kind == "button":
        if control.mode == "toggle":
            return "press", 1  # every message flips the state
        return ("press", 1) if value > 0 else ("release", 0)
    if control.kind == "encoder":
        if control.mode == "rel64":
            delta = value - 64
        else:  # rel2c
            delta = value if value < 64 else value - 128
        return "step", delta
    top = 16383 if msg_type == "pb" else 127
    return "level", value / top
