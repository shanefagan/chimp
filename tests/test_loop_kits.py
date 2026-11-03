"""Kits on disk: reading WAV flavours, importing folders, swapping a pad's sample."""

import struct
import wave
import zipfile

import numpy as np
import pytest

from chimp.loop import kit as kits

RATE = 48000


@pytest.fixture(autouse=True)
def kits_dir(tmp_path, monkeypatch):
    d = tmp_path / "kits"
    monkeypatch.setattr(kits, "USER_DIR", d)
    return d


def pcm16(path, frames=480, rate=RATE, channels=1, level=0.5):
    x = (np.sin(np.arange(frames) / 5) * level * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(x, channels).tobytes())


def float32(path, data, rate=RATE, extensible=False):
    """A float WAV by hand (the wave module can't write one)."""
    data = np.asarray(data, "<f4")
    channels = data.shape[1] if data.ndim == 2 else 1
    body = data.tobytes()
    if extensible:
        guid = struct.pack("<H", 3) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        fmt = struct.pack("<HHIIHH", 0xFFFE, channels, rate, rate * 4 * channels, 4 * channels, 32)
        fmt += struct.pack("<HHI", 22, 32, 0) + guid
    else:
        fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * 4 * channels, 4 * channels, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"LIST" + struct.pack("<I", 3) + b"abc\x00"  # odd-sized chunk: padded
    chunks += b"data" + struct.pack("<I", len(body)) + body
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)


def test_float_and_extensible_wavs_read():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as t:
        stereo = np.stack([np.linspace(-1, 1, 100), np.full(100, 0.25)], axis=1)
        float32(Path(t) / "a.wav", stereo)
        float32(Path(t) / "b.wav", stereo[:, :1], extensible=True)
        a = kits.read_wav(Path(t) / "a.wav", RATE)
        b = kits.read_wav(Path(t) / "b.wav", RATE)
        assert a.shape == (100, 2) and np.allclose(a, stereo, atol=1e-6)
        assert b.shape == (100, 2) and np.allclose(b[:, 1], stereo[:, 0], atol=1e-6)
        (Path(t) / "c.wav").write_bytes(b"not audio at all")
        with pytest.raises(ValueError):
            kits.read_wav(Path(t) / "c.wav", RATE)


def test_written_wavs_read_back():
    import tempfile
    from pathlib import Path

    x = np.stack([np.sin(np.arange(1000) / 7), -np.sin(np.arange(1000) / 7)], axis=1).astype(np.float32) * 0.8
    with tempfile.TemporaryDirectory() as t:
        kits.write_wav(Path(t) / "x.wav", x, RATE)
        assert np.abs(kits.read_wav(Path(t) / "x.wav", RATE) - x).max() < 1e-5


def test_import_copies_the_wavs_and_skips_junk(tmp_path, kits_dir):
    pack = tmp_path / "Heavy Pack!"
    pack.mkdir()
    pcm16(pack / "kick.wav")
    float32(pack / "Boom.WAV", np.zeros((50, 2)) + 0.1)
    (pack / "broken.wav").write_bytes(b"RIFF....nope")
    (pack / "notes.txt").write_text("hi")
    name = kits.import_kit(pack)
    assert name == "Heavy Pack"
    assert sorted(p.name for p in (kits_dir / name).iterdir()) == ["Boom.wav", "kick.wav", "kit.yaml"]
    assert kits.kit_names() == ["synth", "Heavy Pack"]
    k = kits.load(name, RATE)
    assert k.labels()[:2] == ["Kick", "Boom"]
    assert kits.import_kit(pack) == "Heavy Pack 2"  # never overwrites a kit


def test_import_looks_in_sub_folders_when_the_top_has_no_wavs(tmp_path):
    pack = tmp_path / "pack"
    for sub in ("Kicks", "Snares"):
        (pack / sub).mkdir(parents=True)
        pcm16(pack / sub / "01.wav")
    name = kits.import_kit(pack)
    files = sorted(p.name for p in (kits.USER_DIR / name).glob("*.wav"))
    assert len(files) == 2 and "01.wav" in files  # the clash got its folder's name


def test_import_refuses_a_folder_without_wavs(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        kits.import_kit(tmp_path / "empty")
    assert not kits.USER_DIR.exists() or not any(kits.USER_DIR.iterdir())


def test_a_sample_on_the_built_in_kit_makes_an_editable_copy(tmp_path, kits_dir):
    pcm16(tmp_path / "thud.wav", level=0.3)
    name = kits.replace_sample("synth", 2, tmp_path / "thud.wav", RATE)
    assert name == "synth custom"
    folder = kits_dir / name
    assert len(list(folder.glob("*.wav"))) == 16
    k = kits.load(name, RATE)
    labels = [label for _s, label, _n in kits.SLOTS]
    labels[2] = "Thud"
    assert k.labels() == labels
    assert np.abs(k.sounds[2].data).max() == pytest.approx(0.3, abs=0.01)
    assert k.sounds[2].gm_note == 39  # the pad keeps its note
    synth = kits.synth_kit(RATE)
    assert np.abs(k.sounds[0].data - synth.sounds[0].data).max() < 1e-5  # the rest as they were
    assert (folder / "replaced" / "clap.wav").exists()
    assert kits.replace_sample("synth", 0, tmp_path / "thud.wav", RATE) == "synth custom 2"


def test_a_sample_on_your_kit_moves_the_old_one_aside(tmp_path, kits_dir):
    folder = kits_dir / "mine"
    folder.mkdir(parents=True)
    pcm16(folder / "kick.wav", level=0.9)
    pcm16(folder / "crunch.wav", level=0.7)  # spares, in name order: pad 2
    pcm16(folder / "zap.wav", level=0.8)  # pad 3
    pcm16(tmp_path / "new.wav", level=0.2)
    assert kits.replace_sample("mine", 1, tmp_path / "new.wav", RATE) == "mine"
    assert (folder / "kit.yaml").exists()  # the layout is written down from now on
    k = kits.load("mine", RATE)
    assert k.labels()[:3] == ["Kick", "New", "Zap"]  # the others stay put
    assert np.abs(k.sounds[1].data).max() == pytest.approx(0.2, abs=0.01)
    assert (folder / "replaced" / "crunch.wav").exists()
    kits.replace_sample("mine", 1, folder / "new.wav", RATE)  # the same file again
    assert (folder / "new.wav").exists() and (folder / "replaced" / "new.wav").exists()


def test_a_bad_sample_leaves_the_kit_alone(tmp_path, kits_dir):
    (tmp_path / "bad.wav").write_bytes(b"nope")
    with pytest.raises(ValueError):
        kits.replace_sample("synth", 0, tmp_path / "bad.wav", RATE)
    assert not kits_dir.exists()


def test_a_kit_made_by_hand(tmp_path, kits_dir):
    name = kits.new_kit("Sleepy/../Kit")
    assert name == "Sleepy .. Kit" and name in kits.kit_names()  # empty kits are listed too
    assert kits.load(name, RATE).labels() == [""] * 16
    pcm16(tmp_path / "low boom.wav")
    pcm16(tmp_path / "hat.wav")
    kits.replace_sample(name, 4, tmp_path / "low boom.wav", RATE)
    kits.replace_sample(name, 9, tmp_path / "hat.wav", RATE)
    kits.rename_pad(name, 4, "  Sub   drop ", RATE)
    k = kits.load(name, RATE)
    assert k.labels()[4] == "Sub drop" and k.labels()[9] == "Hat" and k.sounds[0] is None
    kits.clear_pad(name, 9, RATE)
    assert kits.load(name, RATE).sounds[9] is None
    assert (kits_dir / name / "replaced" / "hat.wav").exists()


def test_kit_json_is_the_layout_and_stays_inside_the_folder(tmp_path, kits_dir):
    folder = kits_dir / "shared"
    folder.mkdir(parents=True)
    pcm16(folder / "a.wav")
    pcm16(tmp_path / "secret.wav")
    (folder / "kit.yaml").write_text(
        """
format: chimp-kit
version: 1
name: shared
pads:
  - {pad: 16, file: a.wav, label: Last, note: 60}
  - {pad: 1, file: ../secret.wav}
  - {pad: 2, file: /etc/passwd}
  - {pad: 99, file: a.wav}
  - {pad: 3, file: a.wav}
  - not a pad
"""
    )
    k = kits.load("shared", RATE)
    assert k.labels()[15] == "Last" and k.sounds[15].gm_note == 60
    assert k.labels()[2] == "A" and k.sounds[2].gm_note == kits.SLOTS[2][2]
    assert k.sounds[0] is None and k.sounds[1] is None
    for bad in ("format: chimp-kit\nversion: 7\n", "pads: [\n", "- just a list\n", "!!python/object:os.system {}\n"):
        (folder / "kit.yaml").write_text(bad)
        with pytest.raises(ValueError):
            kits.load("shared", RATE)


def test_chimpkit_files_round_trip(tmp_path, kits_dir):
    name = kits.new_kit("Loathe-ish")
    pcm16(tmp_path / "grit.wav", level=0.4)
    kits.replace_sample(name, 7, tmp_path / "grit.wav", RATE)
    kits.rename_pad(name, 7, "Grit crash", RATE)
    out = kits.export_kit(name, tmp_path / "out" / "mine", RATE)
    assert out.name == "mine.chimpkit"
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["grit.wav", "kit.yaml"]
    again = kits.import_kit(out)
    assert again == "Loathe-ish 2"  # named from its kit.yaml
    k = kits.load(again, RATE)
    assert k.labels()[7] == "Grit crash" and np.abs(k.sounds[7].data).max() == pytest.approx(0.4, abs=0.01)
    synth = kits.export_kit("synth", tmp_path / "synth.chimpkit", RATE)
    assert kits.load(kits.import_kit(synth), RATE).labels() == kits.synth_kit(RATE).labels()


def test_a_zip_imports_flat_and_only_wavs(tmp_path, kits_dir):
    pcm16(tmp_path / "k.wav")
    with zipfile.ZipFile(tmp_path / "pack.zip", "w") as z:
        z.write(tmp_path / "k.wav", "Drums/Kicks/kick.wav")
        z.write(tmp_path / "k.wav", "../../evil.wav")
        z.writestr("run.sh", "rm -rf /")
    name = kits.import_kit(tmp_path / "pack.zip")
    assert name == "pack"
    assert sorted(p.name for p in (kits_dir / name).iterdir()) == ["evil.wav", "kick.wav", "kit.yaml"]
    assert not (tmp_path / "evil.wav").exists()
    (tmp_path / "junk.chimpkit").write_bytes(b"nope")
    with pytest.raises(ValueError):
        kits.import_kit(tmp_path / "junk.chimpkit")
