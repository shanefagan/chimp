"""What a MIDI message does in the looper: play a sound, a transport action, or turn
a parameter of the selected pad or of the master bus.

By default any note plays a sound: note 36 is sound 1 (Kick) up to note 51, then it
wraps; and CC 70-77 are eight knobs whose job depends on the knob page: the selected
pad's sound, or the master. A controller Chimp knows (layout.py) maps by position
instead: its pads to the page of sounds its bank or program stands for, its own knobs
to knobs 1-8. "Assign" learns a control for a sound, an action or one parameter, per
device; assigned controls take precedence over all of that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..desktop.config import CONFIG_DIR
from .fx import GLITCHES
from .kit import NUM_SOUNDS

SETTINGS_PATH = CONFIG_DIR / "loop.json"
DEFAULT_BASE_NOTE = 36
DEFAULT_KNOB_CC = 70  # CC 70-77: knobs 1-8 of the current knob page
NUM_KNOBS = 8

ACTIONS: dict[str, str] = {
    "record": "Record / overdub",
    "play": "Play / stop",
    "undo": "Undo layer",
    "clear": "Clear loop",
    "quantise": "Quantise: next setting",
    "metronome": "Metronome on/off",
    "kit": "Next kit",
    "page": "Knobs: next page",
    "sound_next": "Next saved sound",
    "sound_prev": "Previous saved sound",
    # held: act while the button is down
    "roll": "Hold: roll (+ pad)",
    "select": "Hold: select part (+ pad)",
    "mute": "Hold: mute part (+ pad)",
    "solo": "Hold: solo part (+ pad)",
    **{f"g.{mode}": f"Hold: {label.lower()}" for mode, label in GLITCHES},
}
HOLD_ACTIONS = {"roll", "select", "mute", "solo"} | {f"g.{mode}" for mode, _ in GLITCHES}
MODIFIERS = ("select", "mute", "solo")  # held, they change what a pad does


def key_of(msg: list[int]) -> tuple[str, float] | None:
    """('note:ch:num' | 'cc:ch:num' | 'pc:ch:num', velocity 0-1) for a press, None otherwise
    (note-offs, releases, clock and anything else)."""
    if len(msg) < 2:
        return None
    kind, ch = msg[0] & 0xF0, (msg[0] & 0x0F) + 1
    if kind == 0x90 and len(msg) >= 3 and msg[2] > 0:
        return f"note:{ch}:{msg[1]}", msg[2] / 127
    if kind == 0xB0 and len(msg) >= 3 and msg[2] >= 64:
        return f"cc:{ch}:{msg[1]}", 1.0
    if kind == 0xC0:
        return f"pc:{ch}:{msg[1]}", 1.0
    return None


def value_of(msg: list[int]) -> tuple[str, float] | None:
    """('cc:ch:num' | 'pb:ch:0', position 0-1) for anything knob- or fader-like."""
    if len(msg) < 3:
        return None
    kind, ch = msg[0] & 0xF0, (msg[0] & 0x0F) + 1
    if kind == 0xB0:
        return f"cc:{ch}:{msg[1]}", msg[2] / 127
    if kind == 0xE0:
        return f"pb:{ch}:0", (msg[1] | (msg[2] << 7)) / 16383
    return None


def release_of(msg: list[int]) -> str | None:
    """The key a note-off, or a button CC going below 64, releases."""
    if len(msg) < 3:
        return None
    kind, ch = msg[0] & 0xF0, (msg[0] & 0x0F) + 1
    if kind == 0x80 or (kind == 0x90 and msg[2] == 0):
        return f"note:{ch}:{msg[1]}"
    if kind == 0xB0 and msg[2] < 64:
        return f"cc:{ch}:{msg[1]}"
    return None


def describe(key: str) -> str:
    if key.startswith("gpa:"):
        return "Axis " + key[4:].removeprefix("ABS_")
    if key.startswith("gpb:"):
        return "Button " + key[4:].removeprefix("BTN_")
    kind, ch, num = key.split(":")
    name = {"note": f"Note {num}", "cc": f"CC {num}", "pc": f"Program {int(num) + 1}", "pb": "Pitch bend"}[kind]
    return f"{name} · ch {ch}"


@dataclass
class LoopSettings:
    kit: str = "synth"
    bpm: float = 120.0
    bars: int = 0
    quantise: int = 16
    metronome: bool = True
    volume: float = 0.8
    fixed_velocity: bool = False
    knob_page: str = "pad"
    pads_shown: int = 0  # pads on screen: 0 follows the controller, else 8, 12 or 16
    lpd8_jobs: list = field(default_factory=list)  # per drum program: "A", "B"... or "fx" ([] = A, B, fx)
    sound: dict = field(default_factory=dict)  # Engine.sound_settings(): pads and master
    # device -> {key: "sound:N" | action name}
    assign: dict[str, dict[str, str]] = field(default_factory=dict)

    def target(self, device: str, key: str, table: dict[str, str] | None = None) -> str | None:
        """What a press does: 'sound:N', an action name, or None. What you assigned comes
        first, then `table` (what Chimp knows about this controller: layout.py), then the
        defaults for any controller."""
        hit = self.assign.get(device, {}).get(key)
        if hit:
            return hit
        if table and key in table:
            return table[key] or None  # "": a pad Chimp knows has nothing on it
        if key.startswith("note:"):
            note = int(key.rsplit(":", 1)[1])
            return f"sound:{(note - DEFAULT_BASE_NOTE) % NUM_SOUNDS}"
        if key.startswith("cc:"):
            k = int(key.rsplit(":", 1)[1]) - DEFAULT_KNOB_CC
            if 0 <= k < NUM_KNOBS and not self._taken(key):
                return f"knob:{k}"
        return None

    def _taken(self, key: str) -> bool:
        """Assigned on some device: then the CC 70-77 default doesn't apply to it."""
        return any(key in keys for keys in self.assign.values())

    def set_assign(self, device: str, key: str, target: str) -> None:
        dev = self.assign.setdefault(device, {})
        stored = target[7:] if target.startswith("action:") else target
        if not target.startswith("sound:"):
            # one control per action/parameter and device: move it rather than add a second
            for k in [k for k, v in dev.items() if v == stored]:
                del dev[k]
        dev[key] = stored

    def actions_assigned(self) -> dict[str, list[str]]:
        """action -> ['Device · Note 40 · ch 1', ...] for the UI."""
        out: dict[str, list[str]] = {a: [] for a in ACTIONS}
        for device, keys in self.assign.items():
            for key, target in keys.items():
                if target in out:
                    out[target].append(f"{device} · {describe(key)}")
        return out

    def params_assigned(self) -> dict[str, list[str]]:
        """'p.cutoff' / 'm.reverb' -> ['Device · CC 21 · ch 1', ...] (learned knobs only;
        the CC 70-77 knobs follow the knob page)."""
        out: dict[str, list[str]] = {}
        for device, keys in self.assign.items():
            for key, target in keys.items():
                if target.startswith("param:"):
                    out.setdefault(target[6:], []).append(f"{device} · {describe(key)}")
        return out

    def clear_assign(self, target: str) -> None:
        name = target[7:] if target.startswith("action:") else target
        for dev in self.assign.values():
            for k in [k for k, v in dev.items() if v == name]:
                del dev[k]

    @classmethod
    def load(cls, path=SETTINGS_PATH) -> LoopSettings:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        s = cls()
        for k in (
            "kit",
            "bpm",
            "bars",
            "quantise",
            "metronome",
            "volume",
            "assign",
            "fixed_velocity",
            "sound",
            "knob_page",
            "pads_shown",
            "lpd8_jobs",
        ):
            if k in raw:
                setattr(s, k, raw[k])
        return s

    def save(self, path=SETTINGS_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=2, sort_keys=True))
        tmp.replace(path)
