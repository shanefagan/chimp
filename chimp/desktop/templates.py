"""Laying out a device's controls: from what Chimp knows about the controller, or in
plain banks.

Learn finds every note a device sends, but a sampler like the KO II pages its 12 pads
through groups A-D by shifting their notes, so learning gives 48 separate buttons. For a
controller Chimp knows (chimp/controllers), `apply` names those buttons as the hardware
does, puts them in groups (shown as tabs in the editor), and can add the controls that
haven't been learned at all, so a known device works without Learn. Groups are
presentation only: each control still triggers on its own message, so no group has to
be "active".
"""

from __future__ import annotations

import re

from .. import controllers
from ..controllers import Controller
from .config import Control

DEFAULT_NAME = re.compile(r"^(Button|Knob|Fader|Encoder) \d+$")


def _rename(c: Control, name: str) -> None:
    if DEFAULT_NAME.match(c.name):  # never overwrite a name the user chose
        c.name = name


def layout_for(device: str) -> Controller | None:
    """The controller this device is, if Chimp knows what it sends (not just its shape)."""
    c = controllers.find(device)
    return c if c is not None and (c.controls or (c.pads and c.pads.known)) else None


def controls_of(ctrl: Controller) -> list[Control]:
    """The controller's controls as it leaves the factory, in hardware order."""
    out: list[Control] = []
    pads = ctrl.pads
    if pads and pads.known:
        several = len(pads.banks) > 1
        for bank, notes in pads.banks:
            for i, note in enumerate(notes):
                name = f"{bank}{i + 1}" if several else f"Pad {i + 1}"
                group = f"{pads.word} {bank}" if several else "Pads"
                out.append(Control(name, "button", "note", pads.channel, note, "momentary", group=group))
    for s in ctrl.controls:
        group = s.group or ("Controls" if pads and pads.known else "")
        out.append(Control(s.name, s.kind, s.msg, s.channel, s.number, s.mode, group=group))
    return out


def apply(ctrl: Controller, controls: list[Control], add_missing: bool = False) -> tuple[int, int]:
    """Name and group `controls` as the controller lays them out; with add_missing, add
    the ones that aren't there yet. Actions and names you chose are kept.
    Returns (controls added, controls arranged)."""
    have = {c.address: c for c in controls}
    # A device set to another MIDI channel still has the same layout: fall back to the
    # message alone where that picks out one control on each side.
    wanted = controls_of(ctrl)
    loose: dict[tuple, list[Control]] = {}
    for c in controls:
        loose.setdefault((c.msg, c.address[2]), []).append(c)
    once = {k for k in {(w.msg, w.address[2]) for w in wanted} if sum((w.msg, w.address[2]) == k for w in wanted) == 1}
    order: dict[int, int] = {}
    added = arranged = 0
    for i, want in enumerate(wanted):
        c = have.get(want.address)
        key = (want.msg, want.address[2])
        if c is None and key in once and len(loose.get(key, [])) == 1:
            c = loose[key][0]
        if c is None:
            if add_missing:
                controls.append(want)
                order[id(want)] = i
                added += 1
            continue
        order[id(c)] = i
        if c.group != want.group:
            c.group = want.group
            arranged += 1
        _rename(c, want.name)
    # hardware order first; anything the controller's file doesn't mention goes after
    tail = len(wanted)
    controls.sort(key=lambda c: (order.get(id(c), tail), c.group or "~", c.msg, c.channel, c.number))
    return added, arranged


def pad_grid(device: str, group: str, count: int) -> tuple[int, bool]:
    """(columns, bottom-up) to draw `count` controls of a group as the hardware has them:
    a bank of pads keeps the pads' shape; anything else is (0, False), a plain grid."""
    c = controllers.find(device)
    if c is None or c.pads is None:
        return 0, False
    p = c.pads
    is_bank = group == "Pads" or group.startswith(f"{p.word} ")
    if not is_bank or count != p.size:
        return 0, False
    return p.columns, p.first == "bottom-left"


def split_banks(controls: list[Control], size: int) -> int:
    """Put note buttons into banks of `size` in note order (per channel). Returns bank count."""
    notes = sorted((c for c in controls if c.kind == "button" and c.msg == "note"), key=lambda c: (c.channel, c.number))
    banks = 0
    for i, c in enumerate(notes):
        bank = i // size + 1
        banks = max(banks, bank)
        c.group = f"Bank {bank}"
        _rename(c, f"B{bank}·{i % size + 1}")
    controls.sort(key=lambda c: (c.group or "~", c.msg, c.channel, c.number))
    return banks


def clear_groups(controls: list[Control]) -> None:
    for c in controls:
        c.group = ""


def group_names(controls: list[Control]) -> list[str]:
    """Groups in display order; "Other" collects ungrouped controls when some are grouped."""
    names: list[str] = []
    for c in controls:
        if c.group and c.group not in names:
            names.append(c.group)
    if names and any(not c.group for c in controls):
        names.append("Other")
    return names
