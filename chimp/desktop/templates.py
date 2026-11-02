"""Layouts for controllers whose pads come in pages ("groups" / "banks").

Learn finds every note a device sends, but a sampler like the KO II pages its 12 pads
through groups A-D by shifting their notes, so learning gives 48 separate buttons.
These helpers put such buttons into named groups (shown as tabs in the editor) and
give them names that match the hardware. Groups are presentation only: each control
still triggers on its own note, so no group has to be "active".
"""

from __future__ import annotations

import re

from .config import Control

DEFAULT_NAME = re.compile(r"^(Button|Knob|Fader|Encoder) \d+$")


def _rename(c: Control, name: str) -> None:
    if DEFAULT_NAME.match(c.name):  # never overwrite a name the user chose
        c.name = name


def ep133(controls: list[Control]) -> bool:
    """teenage engineering EP-133 K.O. II: groups A-D each shift the 12 pads by 12 notes
    (A = 36-47 ... D = 72-83). Its knobs and fader don't send MIDI."""
    changed = False
    for c in controls:
        if c.msg == "note" and 36 <= c.number <= 83:
            group = "ABCD"[(c.number - 36) // 12]
            c.group = f"Group {group}"
            _rename(c, f"{group}{(c.number - 36) % 12 + 1}")
            changed = True
        elif c.msg == "rt":
            c.group = "Transport"
            changed = True
    controls.sort(key=lambda c: (c.group or "~", c.msg, c.channel, c.number))
    return changed


# ALSA client name -> layout. Matching is by prefix, so "EP-133" also covers "EP-133 #2".
TEMPLATES = {"EP-133": ("KO II: groups A–D", ep133)}
# Overlay grid width per device: the KO II's pads are a 3-wide keypad.
OVERLAY_COLUMNS = {"EP-133": 3}


def overlay_columns(device: str) -> int:
    return next((n for prefix, n in OVERLAY_COLUMNS.items() if device.startswith(prefix)), 0)


def template_for(device: str):
    for prefix, entry in TEMPLATES.items():
        if device.startswith(prefix):
            return entry
    return None


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
