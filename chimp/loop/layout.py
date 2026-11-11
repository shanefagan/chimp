"""The looper shaped like the controller in your hands.

A kit has 16 sounds, but an LPD8 has 8 pads and a KO II 12: showing 16 pads for a
controller with 8 hides which ones you can reach. So the pads on screen come in pages
the size and shape of the controller's pads (page A is sounds 1-8, B is 9-16 for an
8-pad controller), and the page follows the controller: its bank, or for the LPD8 its
program. This module works out the shape and what each pad and knob does; the
controller (controller.py) keeps track of which page is showing.

Where Chimp knows what a controller sends (chimp/controllers), pads map by position:
pad 1 of bank B is the first sound of page B whatever note it sends. The LPD8 mk2's
programs are read from the device, and each drum program is given a job: a page of
sounds, or the FX page, where the pads are roll and the punch-in glitches.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import controllers, protocol
from ..controllers import Controller
from .fx import GLITCHES
from .kit import NUM_SOUNDS
from .programs import FX, job_page, lpd8_jobs  # noqa: F401 - the looper's one place for layout

LPD8 = "LPD8 mk2"
# The FX page's pads, first to last: roll, then the glitches.
FX_PADS: list[str] = ["roll"] + [f"g.{mode}" for mode, _label in GLITCHES]
MAX_KNOBS = 8
SIZES = (8, 12, 16)  # pads-shown choices besides following the controller


@dataclass(frozen=True)
class Shape:
    """How the pads are drawn: like `name`'s."""

    columns: int = 4
    rows: int = 4
    first: str = "bottom-left"
    name: str = ""  # the controller it follows ("" = nothing in particular)
    knobs: int = MAX_KNOBS

    @property
    def size(self) -> int:
        return self.columns * self.rows


def shape_of(device: str) -> Shape | None:
    """The shape of a connected device's pads, if Chimp knows the controller."""
    c = controllers.find(device)
    if c is None or c.pads is None:
        return None
    p = c.pads
    size = min(p.size, NUM_SOUNDS)
    columns = p.columns if p.size <= NUM_SOUNDS else 4  # a 64-pad grid: draw the kit as 4 x 4
    knobs = min(len(c.knobs()), MAX_KNOBS) or MAX_KNOBS
    return Shape(columns, -(-size // columns), p.first, c.name, knobs)


def forced(size: int) -> Shape:
    """A shape for "show me N pads" (12 is the KO II's keypad, three wide)."""
    return Shape(3, 4) if size == 12 else Shape(4, max(1, size // 4))


def page_count(shape: Shape) -> int:
    return -(-NUM_SOUNDS // shape.size)


def page_name(i: int) -> str:
    return chr(ord("A") + i)


def page_sounds(shape: Shape, page: int) -> list[int]:
    return [s for s in range(page * shape.size, (page + 1) * shape.size) if s < NUM_SOUNDS]


def cells(shape: Shape, count: int) -> list[int]:
    """Positions in drawing order (left to right, top row first): which of `count`
    pads (0-based, in the controller's own numbering) sits there, -1 for a gap."""
    out = []
    for row in range(shape.rows):
        r = shape.rows - 1 - row if shape.first == "bottom-left" else row
        for col in range(shape.columns):
            i = r * shape.columns + col
            out.append(i if i < count else -1)
    return out


def _pad_targets(page: int, size: int) -> list[str]:
    """What a bank of `size` pads plays as page `page`: banks past the kit's last page
    start again at the first, and a pad past the last sound has nothing on it ("")."""
    pages = -(-NUM_SOUNDS // size)
    first = (page % pages) * size
    return [f"sound:{first + i}" if first + i < NUM_SOUNDS else "" for i in range(size)]


def table_for(ctrl: Controller) -> dict[str, str]:
    """key -> target for a controller Chimp knows: its pads by position (bank B's first
    pad is page B's first sound), and its first eight knobs as knobs 1-8 ("" = nothing)."""
    out: dict[str, str] = {}
    p = ctrl.pads
    if p and p.known:
        size = min(p.size, NUM_SOUNDS)
        for b, (_name, notes) in enumerate(p.banks):
            targets = _pad_targets(b, size)
            for i, note in enumerate(notes):
                # a grid bigger than the kit (8 x 8) repeats it
                out[f"note:{p.channel}:{note}"] = targets[i % size]
    # Its other controls do nothing until you assign them: a mixer's mute buttons
    # shouldn't play drums because any note does by default.
    for s in ctrl.controls:
        if s.msg in ("cc", "note", "pc", "pb"):
            out[_key(s)] = ""
    for k, s in enumerate(ctrl.knobs()[:MAX_KNOBS]):
        out[_key(s)] = f"knob:{k}"
    return out


def _key(s) -> str:
    return f"{s.msg}:{s.channel}:{0 if s.msg == 'pb' else s.number}"


def lpd8_table(program: protocol.Program, job: str) -> dict[str, str]:
    """key -> target for the LPD8 on `program`: its pads do `job`, whether they send
    notes or CCs, and its knobs are knobs 1-8 whatever CCs they've been given."""

    def channel(c: int) -> int:
        return program.global_channel if c == protocol.GLOBAL_CHANNEL else c

    out: dict[str, str] = {}
    size = len(program.pads)
    targets = FX_PADS[:size] if job == FX else _pad_targets(job_page(job), size)
    for pad, target in zip(program.pads, targets, strict=False):
        ch = channel(pad.channel)
        out[f"note:{ch}:{pad.note}"] = target
        out.setdefault(f"cc:{ch}:{pad.cc}", target)
    for k, knob in enumerate(program.knobs[:MAX_KNOBS]):
        out[f"cc:{channel(knob.channel)}:{knob.cc}"] = f"knob:{k}"  # a knob wins a CC it shares with a pad
    return out
