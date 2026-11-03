"""What a MIDI message does in the looper: play a sound, or a transport action.

By default any note plays a sound: note 36 is sound 1 (Kick) up to note 51, then it
wraps, so an LPD8 program on notes 36-43 plays the first 8 sounds and a KO II group the
first 12. "Assign" learns a control for a sound or an action, per device; assigned
controls take precedence, and a control assigned to an action doesn't play a sound.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..desktop.config import CONFIG_DIR
from .kit import NUM_SOUNDS

SETTINGS_PATH = CONFIG_DIR / "loop.json"
DEFAULT_BASE_NOTE = 36

ACTIONS: dict[str, str] = {
    "record": "Record / overdub",
    "play": "Play / stop",
    "undo": "Undo layer",
    "clear": "Clear loop",
    "quantise": "Quantise: next setting",
    "metronome": "Metronome on/off",
    "kit": "Next kit",
}


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


def describe(key: str) -> str:
    kind, ch, num = key.split(":")
    return {"note": f"Note {num}", "cc": f"CC {num}", "pc": f"Program {int(num) + 1}"}[kind] + f" · ch {ch}"


@dataclass
class LoopSettings:
    kit: str = "drums1"
    bpm: float = 120.0
    bars: int = 0
    quantise: int = 16
    metronome: bool = True
    volume: float = 0.8
    # device -> {key: "sound:N" | action name}
    assign: dict[str, dict[str, str]] = field(default_factory=dict)

    def target(self, device: str, key: str) -> str | None:
        """What a press does: 'sound:N', an action name, or None."""
        hit = self.assign.get(device, {}).get(key)
        if hit:
            return hit
        if key.startswith("note:"):
            note = int(key.rsplit(":", 1)[1])
            return f"sound:{(note - DEFAULT_BASE_NOTE) % NUM_SOUNDS}"
        return None

    def set_assign(self, device: str, key: str, target: str) -> None:
        dev = self.assign.setdefault(device, {})
        if target.startswith("action:"):
            # one control per action and device: move it rather than adding a second
            for k in [k for k, v in dev.items() if v == target[7:]]:
                del dev[k]
            dev[key] = target[7:]
        else:
            dev[key] = target

    def actions_assigned(self) -> dict[str, list[str]]:
        """action -> ['Device · Note 40 · ch 1', ...] for the UI."""
        out: dict[str, list[str]] = {a: [] for a in ACTIONS}
        for device, keys in self.assign.items():
            for key, target in keys.items():
                if target in out:
                    out[target].append(f"{device} · {describe(key)}")
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
        for k in ("kit", "bpm", "bars", "quantise", "metronome", "volume", "assign"):
            if k in raw:
                setattr(s, k, raw[k])
        return s

    def save(self, path=SETTINGS_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=2, sort_keys=True))
        tmp.replace(path)
