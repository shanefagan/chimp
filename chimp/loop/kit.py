"""Drum kits: 16 sounds as float32 stereo arrays.

The built-in kit ("synth") is synthesised at startup (synth.py): no samples are shipped.
Your own kits are folders of WAVs in ~/.local/share/chimp/kits/<name>/: files named after
the slots below land in those slots (kick.wav -> Kick); any other WAVs fill the free slots
in name order.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..desktop.config import DATA_DIR

USER_DIR = DATA_DIR / "kits"
BUILTIN = "synth"
NUM_SOUNDS = 16

# Slot order: the first 8 are what an 8-pad controller plays by default (LPD8 program 1,
# notes 36-43), the next 8 follow on notes 44-51. GM drum notes are for MIDI export.
SLOTS: list[tuple[str, str, int]] = [
    ("kick", "Kick", 36),
    ("snare_1", "Snare", 38),
    ("clap", "Clap", 39),
    ("hihat_1", "Hi-hat", 42),
    ("open_hihat", "Open hat", 46),
    ("rim", "Rim", 37),
    ("tom_1", "Tom hi", 50),
    ("crash", "Crash", 49),
    ("snare_2", "Snare 2", 40),
    ("hihat_2", "Pedal hat", 44),
    ("tom_2", "Tom mid", 47),
    ("tom_3", "Tom lo", 43),
    ("ride", "Ride", 51),
    ("perc_1", "Perc 1", 56),
    ("perc_2", "Perc 2", 54),
    ("perc_3", "Perc 3", 75),
]


@dataclass
class Sound:
    name: str
    gm_note: int
    data: np.ndarray  # float32, shape (frames, 2)


@dataclass
class Kit:
    name: str
    rate: int
    sounds: list[Sound | None] = field(default_factory=lambda: [None] * NUM_SOUNDS)

    def labels(self) -> list[str]:
        return [s.name if s else "" for s in self.sounds]


def kit_dirs() -> list[Path]:
    """Your kit folders (with at least one WAV), by name."""
    if not USER_DIR.is_dir():
        return []
    return [d for d in sorted(USER_DIR.iterdir()) if d.is_dir() and any(d.glob("*.wav")) and d.name != BUILTIN]


def kit_names() -> list[str]:
    """The built-in kit first, then yours."""
    return [BUILTIN] + [d.name for d in kit_dirs()]


def load(name: str, rate: int) -> Kit:
    if name == BUILTIN:
        return synth_kit(rate)
    return load_kit(USER_DIR / name, rate)


def synth_kit(rate: int) -> Kit:
    from . import synth

    kit = Kit(BUILTIN, rate)
    for i, data in enumerate(synth.sounds(rate)):
        _stem, label, note = SLOTS[i]
        kit.sounds[i] = Sound(label, note, data)
    return kit


def read_wav(path: Path, rate: int) -> np.ndarray:
    """A WAV as float32 stereo at `rate` (8/16/24/32-bit PCM, mono or stereo)."""
    with wave.open(str(path), "rb") as w:
        channels, width, src_rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v & 0x800000, v - 0x1000000, v)).astype(np.float32) / 8388608
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    else:
        raise ValueError(f"{path.name}: {width * 8}-bit samples aren't supported")
    x = x.reshape(-1, channels)
    x = np.repeat(x, 2, axis=1) if channels == 1 else x[:, :2]
    if src_rate != rate and len(x) > 1:
        n = round(len(x) * rate / src_rate)
        pos = np.linspace(0, len(x) - 1, n)
        x = np.stack([np.interp(pos, np.arange(len(x)), x[:, c]) for c in range(2)], axis=1)
    return np.ascontiguousarray(x, dtype=np.float32)


def load_kit(folder: Path, rate: int) -> Kit:
    kit = Kit(folder.name, rate)
    wavs = {p.stem.lower(): p for p in sorted(folder.glob("*.wav"))}
    used: set[str] = set()
    for i, (stem, label, note) in enumerate(SLOTS):
        if stem in wavs:
            kit.sounds[i] = Sound(label, note, read_wav(wavs[stem], rate))
            used.add(stem)
    spare = [stem for stem in wavs if stem not in used]
    for i in range(NUM_SOUNDS):
        if kit.sounds[i] is None and spare:
            stem = spare.pop(0)
            kit.sounds[i] = Sound(stem.replace("_", " ").capitalize(), SLOTS[i][2], read_wav(wavs[stem], rate))
    return kit
