"""Loops on disk: save/load (JSON), export to WAV and to a standard MIDI file."""

from __future__ import annotations

import json
import re
import struct
import wave
from pathlib import Path

import numpy as np

from ..desktop.config import DATA_DIR
from .engine import Engine, Hit

LOOPS_DIR = DATA_DIR / "loops"
EXPORT_DIR = Path.home() / "Music" / "Chimp"
PPQ = 480


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w .-]+", "", name).strip(" .")
    return name[:60] or "loop"


# --- save / load --------------------------------------------------------------------


def to_dict(e: Engine, kit_name: str) -> dict:
    with e.lock:
        r = float(e.rate)
        return {
            "version": 1,
            "kit": kit_name,
            "bpm": e.bpm,
            "bars": e.bars,
            "quantise": e.quantise,
            "length_s": e.length / r,
            # Layers kept apart so undo still works after loading.
            "layers": [[[round(h.t / r, 6), h.sound, round(h.vel, 4)] for h in layer] for layer in e.layers if layer],
        }


def from_dict(e: Engine, d: dict) -> None:
    r = e.rate
    layers = [[Hit(round(t * r), int(s), float(v), -(10**12)) for t, s, v in layer] for layer in d.get("layers", [])]
    e.bars = int(d.get("bars", 0))
    e.quantise = int(d.get("quantise", 16))
    e.load(round(float(d["length_s"]) * r), float(d.get("bpm", 120.0)), layers)


def save(e: Engine, kit_name: str, name: str) -> Path:
    LOOPS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOOPS_DIR / f"{safe_name(name)}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(to_dict(e, kit_name), indent=1))
    tmp.replace(path)
    return path


def saved() -> list[str]:
    if not LOOPS_DIR.is_dir():
        return []
    files = sorted(LOOPS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.stem for p in files]


def read(name: str) -> dict:
    return json.loads((LOOPS_DIR / f"{safe_name(name)}.json").read_text())


def delete(name: str) -> None:
    (LOOPS_DIR / f"{safe_name(name)}.json").unlink(missing_ok=True)


# --- export ---------------------------------------------------------------------------


def render(e: Engine, repeats: int = 4) -> np.ndarray:
    """The loop played `repeats` times, float32 stereo. One silent pass first, so sounds
    ringing over the loop point are there from the start and the file loops seamlessly."""
    with e.lock:
        if not e.length:
            raise ValueError("nothing recorded")
        off = Engine(e.rate)
        off.kit, off.volume, off.bpm, off.quantise = e.kit, e.volume, e.bpm, e.quantise
        off.metronome = False
        off.layers = [[Hit(h.t, h.sound, h.vel, -(10**12)) for h in layer] for layer in e.layers]
        off.length, off.state = e.length, "playing"
    block = 1024
    total = e.length * (repeats + 1)
    out = np.concatenate([off.render(block) for _ in range(-(-total // block))])
    return out[e.length : total]


def export_wav(e: Engine, path: Path, repeats: int = 4) -> Path:
    audio = render(e, repeats)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(e.rate)
        w.writeframes(pcm.tobytes())
    return path


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def midi_bytes(e: Engine, repeats: int = 1) -> bytes:
    """Type-0 SMF: the hits (quantised as set) on channel 10 with General MIDI drum notes,
    the loop's tempo, and an end exactly one loop (x repeats) long."""
    with e.lock:
        if not e.length:
            raise ValueError("nothing recorded")
        frames_per_tick = e.beat_frames() / PPQ
        loop_ticks = round(e.length / frames_per_tick)
        notes = []
        for h in (h for layer in e.layers for h in layer):
            s = e.kit.sounds[h.sound] if e.kit else None
            note = s.gm_note if s else 36 + h.sound
            tick = round(e.placed(h.t) / frames_per_tick)
            vel = max(1, min(127, round(h.vel * 127)))
            notes.extend((tick + k * loop_ticks, note, vel) for k in range(repeats))
        tempo = round(60_000_000 / e.bpm)
    events: list[tuple[int, int, bytes]] = []  # (tick, order, data): offs before ons at a tick
    gate = PPQ // 8
    for tick, note, vel in notes:
        events.append((tick, 1, bytes([0x99, note, vel])))
        events.append((tick + gate, 0, bytes([0x89, note, 0])))
    events.sort(key=lambda ev: (ev[0], ev[1]))
    track = bytearray(b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big"))
    track += b"\x00\xff\x58\x04\x04\x02\x18\x08"  # 4/4
    now = 0
    for tick, _, data in events:
        track += _vlq(max(0, tick - now)) + data
        now = max(now, tick)
    end = max(loop_ticks * repeats, now)
    track += _vlq(end - now) + b"\xff\x2f\x00"
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ)
    return header + b"MTrk" + struct.pack(">I", len(track)) + bytes(track)


def export_midi(e: Engine, path: Path, repeats: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(midi_bytes(e, repeats))
    return path
