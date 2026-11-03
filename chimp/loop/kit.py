"""Drum kits: 16 sounds as float32 stereo arrays.

The built-in kit ("synth") is synthesised at startup (synth.py): no samples are shipped.
Your own kits are folders of WAVs in ~/.local/share/chimp/kits/<name>/: files named after
the slots below land in those slots (kick.wav -> Kick); any other WAVs fill the free slots
in name order, unless the folder has a kit.yaml (the kit format: see "kits on disk").
Kits are made, imported, exported and edited pad by pad from the app; the built-in kit is
never changed, it's copied first.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import wave
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

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
    """Your kit folders (with a kit.yaml or at least one WAV), by name."""
    if not USER_DIR.is_dir():
        return []
    return [
        d
        for d in sorted(USER_DIR.iterdir())
        if d.is_dir() and d.name != BUILTIN and ((d / MANIFEST).is_file() or _wavs(d))
    ]


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


# --- reading and writing WAVs ---------------------------------------------------------

_PCM, _FLOAT, _EXTENSIBLE = 1, 3, 0xFFFE


def _wav_parts(path: Path) -> tuple[int, int, int, int, bytes]:
    """(format, channels, rate, bits, sample bytes) from a RIFF/WAVE file.

    Parsed by hand: the `wave` module only reads integer PCM, and many sample packs
    are 32-bit float or WAVE_FORMAT_EXTENSIBLE.
    """
    blob = path.read_bytes()
    if len(blob) < 12 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError(f"{path.name}: not a WAV file")
    fmt = data = None
    pos = 12
    while pos + 8 <= len(blob):
        cid, size = blob[pos : pos + 4], int.from_bytes(blob[pos + 4 : pos + 8], "little")
        body = blob[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or len(fmt) < 16 or data is None:
        raise ValueError(f"{path.name}: no audio in this WAV")
    tag = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    src_rate = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if tag == _EXTENSIBLE and len(fmt) >= 26:
        tag = int.from_bytes(fmt[24:26], "little")  # the sub-format GUID starts with the tag
    if channels < 1 or src_rate < 1 or bits < 1:
        raise ValueError(f"{path.name}: broken WAV header")
    return tag, channels, src_rate, bits, data


def read_wav(path: Path, rate: int) -> np.ndarray:
    """A WAV as float32 stereo at `rate`: 8/16/24/32-bit PCM or 32/64-bit float, any channels."""
    tag, channels, src_rate, bits, raw = _wav_parts(path)
    width = (bits + 7) // 8
    raw = raw[: len(raw) // (width * channels) * width * channels]
    if tag == _FLOAT and bits in (32, 64):
        x = np.frombuffer(raw, "<f4" if bits == 32 else "<f8").astype(np.float32)
        x = np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
    elif tag == _PCM and width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif tag == _PCM and width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif tag == _PCM and width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v & 0x800000, v - 0x1000000, v)).astype(np.float32) / 8388608
    elif tag == _PCM and width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    else:
        kind = "float" if tag == _FLOAT else "PCM" if tag == _PCM else f"format {tag:#x}"
        raise ValueError(f"{path.name}: {bits}-bit {kind} isn't supported")
    if not len(x):
        raise ValueError(f"{path.name}: empty WAV")
    x = x.reshape(-1, channels)
    x = np.repeat(x, 2, axis=1) if channels == 1 else x[:, :2]
    if src_rate != rate and len(x) > 1:
        n = round(len(x) * rate / src_rate)
        pos = np.linspace(0, len(x) - 1, n)
        x = np.stack([np.interp(pos, np.arange(len(x)), x[:, c]) for c in range(2)], axis=1)
    return np.ascontiguousarray(x, dtype=np.float32)


def write_wav(path: Path, data: np.ndarray, rate: int) -> None:
    """Stereo float data as a 24-bit PCM WAV."""
    v = np.round(np.clip(data, -1.0, 1.0 - 1 / 8388608) * 8388608).astype("<i4")
    b = v.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(rate)
        w.writeframes(np.ascontiguousarray(b).tobytes())


# --- kits on disk -----------------------------------------------------------------------
#
# A kit is a folder. Its kit.yaml says what's on each pad:
#
#   format: chimp-kit
#   version: 1
#   name: Heavy
#   pads:
#     - pad: 1
#       file: kick.wav
#       label: Kick
#       note: 36
#
# "file" is a WAV in the same folder (a plain name: no paths). Pads not listed are empty;
# "label" and "note" are optional (the WAV's name, the slot's General MIDI note). A folder
# without kit.yaml is read by file name (see the top of this file) and gets a kit.yaml the
# first time it's edited. A .chimpkit file is a kit folder zipped up, to share.

MANIFEST = "kit.yaml"
FORMAT = "chimp-kit"
VERSION = 1
EXTENSION = ".chimpkit"


@dataclass
class Pad:
    label: str
    file: Path
    note: int


def _wavs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.suffix.lower() == ".wav" and p.is_file())


def _pretty(stem: str) -> str:
    return " ".join(stem.replace("_", " ").replace("-", " ").split()).capitalize() or "Sound"


def _safe_file(name: object) -> str | None:
    """A plain file name in the kit folder, or None: shared kits mustn't reach outside it."""
    if not isinstance(name, str) or not name or name != Path(name).name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or not name.lower().endswith(".wav"):
        return None
    return name


def _by_name(folder: Path) -> list[Pad | None]:
    """The file-name rules: slot stems first, the other WAVs fill the gaps in name order."""
    wavs: dict[str, Path] = {}
    for p in _wavs(folder):
        wavs.setdefault(p.stem.lower(), p)
    out: list[Pad | None] = [None] * NUM_SOUNDS
    used: set[str] = set()
    for i, (stem, label, note) in enumerate(SLOTS):
        if stem in wavs:
            out[i] = Pad(label, wavs[stem], note)
            used.add(stem)
    spare = [stem for stem in wavs if stem not in used]
    for i in range(NUM_SOUNDS):
        if out[i] is None and spare:
            p = wavs[spare.pop(0)]
            out[i] = Pad(_pretty(p.stem), p, SLOTS[i][2])
    return out


def _parse_manifest(d: object, folder: Path) -> list[Pad | None]:
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise ValueError(f"{MANIFEST} isn't a Chimp kit")
    if not isinstance(d.get("version"), int) or d["version"] > VERSION:
        raise ValueError(f"{MANIFEST} is from a newer Chimp (version {d.get('version')})")
    out: list[Pad | None] = [None] * NUM_SOUNDS
    for e in d.get("pads") or []:
        if not isinstance(e, dict):
            continue
        i, name = e.get("pad"), _safe_file(e.get("file"))
        if not isinstance(i, int) or not 1 <= i <= NUM_SOUNDS or name is None:
            continue
        note = e.get("note")
        note = note if isinstance(note, int) and 0 <= note <= 127 else SLOTS[i - 1][2]
        label = e.get("label") if isinstance(e.get("label"), str) and e["label"].strip() else _pretty(Path(name).stem)
        out[i - 1] = Pad(label.strip()[:40], folder / name, note)
    return out


def pads(folder: Path) -> list[Pad | None]:
    """What's on each pad of the kit in `folder`."""
    m = folder / MANIFEST
    if m.is_file():
        try:
            return _parse_manifest(yaml.safe_load(m.read_text(encoding="utf-8")), folder)
        except yaml.YAMLError as exc:
            raise ValueError(f"{MANIFEST}: {exc}") from exc
    return _by_name(folder)


def _dump(d: dict) -> str:
    return "# A Chimp Loop kit: which WAV in this folder plays on which pad.\n" + yaml.safe_dump(
        d, sort_keys=False, allow_unicode=True
    )


def write_manifest(folder: Path, entries: list[Pad | None], name: str | None = None) -> None:
    d = {
        "format": FORMAT,
        "version": VERSION,
        "name": name or folder.name,
        "pads": [
            {"pad": i + 1, "file": p.file.name, "label": p.label, "note": p.note}
            for i, p in enumerate(entries)
            if p is not None
        ],
    }
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(_dump(d), encoding="utf-8")
    tmp.replace(folder / MANIFEST)


def load_kit(folder: Path, rate: int) -> Kit:
    kit = Kit(folder.name, rate)
    for i, p in enumerate(pads(folder)):
        if p is not None and p.file.is_file():
            kit.sounds[i] = Sound(p.label, p.note, read_wav(p.file, rate))
    return kit


def clean_name(name: str) -> str:
    """A kit name that's safe as a folder name."""
    name = "".join(c if c.isalnum() or c in " -_." else " " for c in name)
    name = " ".join(name.split()).strip(" .")
    return name[:60] or "kit"


def unique_name(name: str) -> str:
    """`name`, or `name 2`, `name 3`… so it's not the built-in kit or an existing folder."""
    base = clean_name(name)
    candidate, n = base, 1
    while candidate.lower() == BUILTIN or (USER_DIR / candidate).exists():
        n += 1
        candidate = f"{base} {n}"
    return candidate


def _folder(kit: str) -> Path:
    folder = USER_DIR / kit
    if kit == BUILTIN or not folder.is_dir():
        raise ValueError(f"no kit of yours called {kit}")
    return folder


def new_kit(name: str) -> str:
    """An empty kit of yours, to fill pad by pad; returns its name."""
    kit = unique_name(name)
    folder = USER_DIR / kit
    folder.mkdir(parents=True)
    write_manifest(folder, [None] * NUM_SOUNDS, name)
    return kit


def _readable(paths: list[Path]) -> list[Path]:
    good = []
    for p in paths:
        try:
            _wav_parts(p)
        except (OSError, ValueError):
            continue
        good.append(p)
    return good


def _import_folder(src: Path, name: str | None) -> str:
    m = src / MANIFEST
    if m.is_file():  # a Chimp kit already: keep its layout
        entries = pads(src)
        files = _readable(sorted({p.file for p in entries if p and p.file.is_file()}))
        if not files:
            raise ValueError(f"no WAVs Chimp can read in {src.name}")
        kit = unique_name(name or src.name)
        dest = USER_DIR / kit
        dest.mkdir(parents=True)
        for f in files:
            shutil.copyfile(f, dest / f.name)
        keep = [Pad(p.label, dest / p.file.name, p.note) if p and p.file in files else None for p in entries]
        write_manifest(dest, keep, name or src.name)
        return kit
    found = _wavs(src) or sorted(p for p in src.rglob("*") if p.suffix.lower() == ".wav" and p.is_file())
    good = _readable(found)
    if not good:
        raise ValueError(f"no WAVs Chimp can read in {src.name}")
    kit = unique_name(name or src.name)
    dest = USER_DIR / kit
    dest.mkdir(parents=True)
    taken: set[str] = set()
    for p in good:
        stem = p.stem
        if stem.lower() in taken:  # the same name in two sub-folders
            stem = f"{p.parent.name}_{p.stem}"
        k, base = 1, stem
        while stem.lower() in taken:
            k += 1
            stem = f"{base}_{k}"
        taken.add(stem.lower())
        shutil.copyfile(p, dest / f"{stem}.wav")
    write_manifest(dest, _by_name(dest), name or src.name)
    return kit


def _import_package(src: Path, name: str | None) -> str:
    """A .chimpkit (or a .zip of WAVs): only kit.yaml and WAVs come out, flattened."""
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{src.name} isn't a kit file") from exc
    with z, tempfile.TemporaryDirectory() as tmp:
        out, total = Path(tmp) / "kit", 0
        out.mkdir()
        manifest = None
        for info in z.infolist():
            if info.is_dir():
                continue
            base = Path(info.filename.replace("\\", "/")).name
            total += info.file_size
            if total > 2 << 30:
                raise ValueError(f"{src.name} is too big to be a drum kit")
            if base == MANIFEST and manifest is None:
                manifest = z.read(info)
            elif _safe_file(base) and not (out / base).exists():
                (out / base).write_bytes(z.read(info))
        title = name
        if manifest is not None:
            (out / MANIFEST).write_bytes(manifest)
            with contextlib.suppress(yaml.YAMLError, AttributeError):
                title = title or yaml.safe_load(manifest).get("name")
        return _import_folder(out, title if isinstance(title, str) and title.strip() else src.stem)


def import_kit(src: Path, name: str | None = None) -> str:
    """A folder of WAVs, a Chimp kit folder, a .chimpkit or a .zip becomes a kit of yours.

    Everything is copied into your kits folder; returns the new kit's name. In a plain
    folder, WAVs at the top are used, or if there are none the WAVs anywhere below
    (sample packs often keep one sound per sub-folder). Files that won't read are skipped.
    """
    src = Path(src)
    if src.is_dir():
        return _import_folder(src, name)
    if src.is_file() and src.suffix.lower() in (EXTENSION, ".zip"):
        return _import_package(src, name)
    raise ValueError(f"{src.name}: pick a folder of WAVs, a {EXTENSION} or a .zip")


def export_kit(kit: str, dest: Path, rate: int) -> Path:
    """The kit as one .chimpkit file at `dest` (the built-in kit is rendered to WAVs)."""
    dest = Path(dest)
    if dest.suffix.lower() != EXTENSION:
        dest = dest.with_name(dest.name + EXTENSION)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        if kit == BUILTIN:
            folder = Path(tmp)
            _write_synth(folder, rate)
            entries = _by_name(folder)
        else:
            folder = _folder(kit)
            entries = pads(folder)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            d = {
                "format": FORMAT,
                "version": VERSION,
                "name": kit,
                "pads": [
                    {"pad": i + 1, "file": p.file.name, "label": p.label, "note": p.note}
                    for i, p in enumerate(entries)
                    if p is not None and p.file.is_file()
                ],
            }
            z.writestr(MANIFEST, _dump(d))
            for f in sorted({p.file for p in entries if p is not None and p.file.is_file()}):
                z.write(f, f.name)
    return dest


def _write_synth(folder: Path, rate: int) -> None:
    for i, sound in enumerate(synth_kit(rate).sounds):
        if sound is not None:
            write_wav(folder / f"{SLOTS[i][0]}.wav", sound.data, rate)


def editable_copy(rate: int) -> str:
    """The built-in kit written out as WAVs, as a new kit of yours; returns its name."""
    kit = unique_name(f"{BUILTIN} custom")
    dest = USER_DIR / kit
    dest.mkdir(parents=True)
    _write_synth(dest, rate)
    write_manifest(dest, _by_name(dest))
    return kit


def _editable(kit: str, rate: int) -> tuple[str, Path, list[Pad | None]]:
    """Kit name, folder and pads, ready to change: the built-in kit is copied first."""
    if kit == BUILTIN:
        kit = editable_copy(rate)
    folder = _folder(kit)
    return kit, folder, pads(folder)


def _put_aside(path: Path, entries: list[Pad | None]) -> None:
    """Move a WAV no pad uses any more into replaced/ (never deleted)."""
    if not path.is_file() or any(p is not None and p.file == path for p in entries):
        return
    bin_ = path.parent / "replaced"
    bin_.mkdir(exist_ok=True)
    moved, n = bin_ / path.name, 1
    while moved.exists():
        n += 1
        moved = bin_ / f"{path.stem} {n}{path.suffix}"
    path.rename(moved)


def replace_sample(kit: str, slot: int, src: Path, rate: int) -> str:
    """Put the WAV `src` on pad `slot` of `kit`; returns the kit it went into.

    The built-in kit becomes an editable copy first. The pad takes the WAV's name; the
    file it replaces is moved into the kit's `replaced/` folder, never deleted.
    """
    src = Path(src)
    if not 0 <= slot < NUM_SOUNDS:
        raise ValueError(f"no pad {slot + 1}")
    read_wav(src, rate)  # refuse anything that won't play before touching the kit
    blob = src.read_bytes()  # read now: src may be the very file that gets moved aside
    kit, folder, entries = _editable(kit, rate)
    old, entries[slot] = entries[slot], None
    if old is not None:
        _put_aside(old.file, entries)
    stem = clean_name(src.stem).replace(" ", "_")
    target, n = folder / f"{stem}.wav", 1
    while target.exists():
        n += 1
        target = folder / f"{stem}_{n}.wav"
    target.write_bytes(blob)
    entries[slot] = Pad(_pretty(src.stem), target, old.note if old else SLOTS[slot][2])
    write_manifest(folder, entries)
    return kit


def rename_pad(kit: str, slot: int, label: str, rate: int) -> str:
    kit, folder, entries = _editable(kit, rate)
    p = entries[slot]
    if p is not None and label.strip():
        p.label = " ".join(label.split())[:40]
        write_manifest(folder, entries)
    return kit


def clear_pad(kit: str, slot: int, rate: int) -> str:
    kit, folder, entries = _editable(kit, rate)
    old, entries[slot] = entries[slot], None
    if old is not None:
        _put_aside(old.file, entries)
    write_manifest(folder, entries)
    return kit
