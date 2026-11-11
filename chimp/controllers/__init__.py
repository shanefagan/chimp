"""What Chimp knows about particular controllers: how many pads, in what shape, in how
many banks, and what their knobs, faders and buttons send out of the box.

Each controller is a YAML file: the ones here ship with Chimp; your own go in
~/.local/share/chimp/controllers/ (a file there with the same name replaces ours).

    name: Korg nanoKONTROL2          # shown in the app
    match: [nanoKONTROL2]            # the device's MIDI name contains one of these
    about: Factory settings.         # one line: where the numbers come from
    pads:                            # drum pads, if it has them
      columns: 4
      rows: 2
      first: bottom-left             # where pad 1 is: bottom-left (default) or top-left
      channel: 10
      banks: {A: 36, B: 44}          # bank: its first note (the rest count up) or a
                                     # list of notes; or just a number of banks when
                                     # the notes aren't known (the shape still is)
      word: Bank                     # what the device calls them (Group, Scene...)
    controls:                        # everything else, in the order it's laid out
      - name: "Fader {n}"            # {n} counts from 1 (or `start:`)
        kind: fader                  # knob | fader | encoder | button
        cc: 0-7                      # or note: / pc: ; a number, a list or a range
        channel: 1
        group: Faders                # optional: a tab in the editor
        mode: absolute               # optional: momentary | toggle (buttons),
                                     # rel64 | rel2c (encoders)
      - {name: Play, kind: button, rt: start}   # MIDI start / continue / stop

The editor uses this to set a device up without Learn (and to name and group what
Learn finds); the overlay and Chimp Loop use the pads' shape. Numbers are factory
settings: a device you've reprogrammed is still best taught with Learn.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

BUILTIN_DIR = Path(__file__).resolve().parent
KINDS = ("knob", "fader", "encoder", "button")
MODES = {
    "knob": ("absolute",),
    "fader": ("absolute",),
    "encoder": ("rel64", "rel2c"),
    "button": ("momentary", "toggle"),
}
RT = {"start": 0xFA, "continue": 0xFB, "stop": 0xFC}


def user_dir() -> Path:
    if os.environ.get("CHIMP_CONTROLLERS_DIR"):
        return Path(os.environ["CHIMP_CONTROLLERS_DIR"])
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data / "chimp" / "controllers"


@dataclass(frozen=True)
class Pads:
    columns: int
    rows: int
    first: str  # "bottom-left" | "top-left"
    channel: int  # 1-16; 0 when the notes aren't known
    banks: tuple[tuple[str, tuple[int, ...]], ...]  # (name, notes); notes () when unknown
    word: str = "Bank"  # what the device calls a bank: Bank, Group, Scene, Program

    @property
    def size(self) -> int:
        return self.columns * self.rows

    @property
    def known(self) -> bool:
        """Do we know what the pads send, or only their shape?"""
        return any(notes for _name, notes in self.banks)


@dataclass(frozen=True)
class Spec:
    """One control as it leaves the factory."""

    name: str
    kind: str
    msg: str  # cc | note | pc | pb | rt
    channel: int
    number: int
    mode: str
    group: str


@dataclass(frozen=True)
class Controller:
    id: str  # the file's name
    name: str
    match: tuple[str, ...]
    about: str
    pads: Pads | None
    controls: tuple[Spec, ...]
    mine: bool  # from your own folder

    def knobs(self) -> list[Spec]:
        """What Chimp Loop treats as knobs 1-8: the knobs, or failing that the faders."""
        for kind in ("knob", "fader"):
            hit = [s for s in self.controls if s.kind == kind and s.msg in ("cc", "pb")]
            if hit:
                return hit
        return []


class Invalid(ValueError):
    pass


def _numbers(v) -> list[int]:
    """7, [1, 2, 3] or "0-7" as a list."""
    if isinstance(v, bool):
        raise Invalid(f"{v!r} isn't a number")
    if isinstance(v, int):
        out = [v]
    elif isinstance(v, str) and re.fullmatch(r"\s*\d+\s*-\s*\d+\s*", v):
        a, b = (int(x) for x in v.split("-"))
        out = list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
    elif isinstance(v, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
        out = list(v)
    else:
        raise Invalid(f"{v!r} isn't a number, a list of numbers or a range like 0-7")
    if not out or len(out) > 128 or not all(0 <= x <= 127 for x in out):
        raise Invalid(f"{v!r}: MIDI numbers are 0-127")
    return out


def _channel(v, default: int = 1) -> int:
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 16:
        raise Invalid(f"channel {v!r}: channels are 1-16")
    return v


def _text(v, limit: int = 80) -> str:
    return " ".join(str(v).split())[:limit] if isinstance(v, (str, int)) and not isinstance(v, bool) else ""


def _pads(d) -> Pads | None:
    if d is None:
        return None
    if not isinstance(d, dict):
        raise Invalid("pads: needs columns and rows")
    cols, rows = d.get("columns"), d.get("rows")
    if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 16 for n in (cols, rows)):
        raise Invalid("pads: columns and rows are numbers from 1 to 16")
    size = cols * rows
    first = d.get("first", "bottom-left")
    if first not in ("bottom-left", "top-left"):
        raise Invalid("pads: first is bottom-left or top-left")
    raw = d.get("banks", 1)
    banks: list[tuple[str, tuple[int, ...]]] = []
    if isinstance(raw, int) and not isinstance(raw, bool):
        if not 1 <= raw <= 16:
            raise Invalid("pads: 1 to 16 banks")
        banks = [(chr(ord("A") + i) if raw > 1 else "", ()) for i in range(raw)]
    elif isinstance(raw, dict) and raw:
        for name, v in raw.items():
            if isinstance(v, int) and not isinstance(v, bool):
                v = f"{v}-{v + size - 1}"
            notes = _numbers(v)
            if len(notes) != size:
                raise Invalid(f"pads: bank {name} has {len(notes)} notes for {size} pads")
            banks.append((_text(name, 12), tuple(notes)))
    else:
        raise Invalid("pads: banks is a number, or names with their notes")
    known = any(notes for _n, notes in banks)
    word = _text(d.get("word", "Bank"), 12) or "Bank"
    return Pads(cols, rows, first, _channel(d.get("channel"), 10) if known else 0, tuple(banks), word)


def _controls(items) -> tuple[Spec, ...]:
    if items is None:
        return ()
    if not isinstance(items, list):
        raise Invalid("controls: a list")
    out: list[Spec] = []
    for it in items:
        if not isinstance(it, dict):
            raise Invalid(f"controls: {it!r} isn't a control")
        kind = it.get("kind")
        if kind not in KINDS:
            raise Invalid(f"controls: kind is one of {', '.join(KINDS)}")
        sent = [k for k in ("cc", "note", "pc", "rt", "pb") if k in it]
        if len(sent) != 1:
            raise Invalid(f"controls: {it.get('name', kind)!r} needs one of cc, note, pc, pb or rt")
        msg = sent[0]
        if msg == "rt":
            if it["rt"] not in RT:
                raise Invalid("controls: rt is start, continue or stop")
            numbers = [RT[it["rt"]]]
        elif msg == "pb":
            numbers = [0]
        else:
            numbers = _numbers(it[msg])
        allowed = ("cc", "note", "pc", "rt") if kind == "button" else ("cc",) if kind == "encoder" else ("cc", "pb")
        if msg not in allowed:
            raise Invalid(f"controls: a {kind} can't send {msg}")
        mode = it.get("mode", MODES[kind][0])
        if mode not in MODES[kind]:
            raise Invalid(f"controls: a {kind}'s mode is one of {', '.join(MODES[kind])}")
        name = _text(it.get("name") or kind.capitalize() + " {n}", 40)
        if len(numbers) > 1 and "{n}" not in name:
            name += " {n}"
        channel = _channel(it.get("channel"))
        group = _text(it.get("group", ""), 40)
        start = it.get("start", 1)
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 999:
            raise Invalid(f"controls: start {start!r} isn't a number to count from")
        for i, number in enumerate(numbers):
            out.append(Spec(name.replace("{n}", str(start + i)), kind, msg, channel, number, mode, group))
    return tuple(out)


def parse(d, ident: str = "", mine: bool = False) -> Controller:
    """A controller from its YAML, or Invalid saying what's wrong with it."""
    if not isinstance(d, dict):
        raise Invalid("not a controller (no name, match...)")
    name = _text(d.get("name"))
    match = d.get("match")
    if isinstance(match, str):
        match = [match]
    if not name or not isinstance(match, list) or not match or not all(isinstance(m, str) and _norm(m) for m in match):
        raise Invalid("needs a name, and match: the names the device shows up as")
    pads, controls = _pads(d.get("pads")), _controls(d.get("controls"))
    if pads is None and not controls:
        raise Invalid("has neither pads nor controls")
    seen: set[tuple] = set()
    for key in _addresses(pads, controls):
        if key in seen:
            raise Invalid(f"two controls send {key[0]} {key[2]} on channel {key[1]}")
        seen.add(key)
    return Controller(ident, name, tuple(match), _text(d.get("about"), 200), pads, controls, mine)


def _addresses(pads: Pads | None, controls: tuple[Spec, ...]):
    if pads:
        for _name, notes in pads.banks:
            for n in notes:
                yield ("note", pads.channel, n)
    for s in controls:
        yield (s.msg, s.channel, s.number)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_cache: list[Controller] | None = None


def _warn(text: str) -> None:
    print(f"controllers: {text}", file=sys.stderr, flush=True)


def load_all(folders: list[tuple[Path, bool]] | None = None) -> list[Controller]:
    """Every controller Chimp knows (yours win over ours of the same file name)."""
    found: dict[str, Controller] = {}
    for folder, mine in folders or [(BUILTIN_DIR, False), (user_dir(), True)]:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            try:
                found[path.stem] = parse(yaml.safe_load(path.read_text(encoding="utf-8")), path.stem, mine)
            except (OSError, yaml.YAMLError, Invalid) as exc:
                _warn(f"{path}: {exc}")
    return sorted(found.values(), key=lambda c: c.name.lower())


def known() -> list[Controller]:
    global _cache
    if _cache is None:
        _cache = load_all()
    return _cache


def reset() -> None:
    """Read the files again next time (tests, or after adding one)."""
    global _cache
    _cache = None


def find(device: str) -> Controller | None:
    """The controller a MIDI device name belongs to: the longest match wins, so
    "APC mini mk2" isn't taken for an "APC mini"; yours win ties."""
    name = _norm(device)
    best: tuple[int, bool] | None = None
    hit = None
    for c in known():
        for m in c.match:
            if _norm(m) in name:
                rank = (len(_norm(m)), c.mine)
                if best is None or rank > best:
                    best, hit = rank, c
    return hit
