"""Live check of the looper's MIDI path (needs ALSA; no sound card, nothing audible).

A virtual MIDI port plays a pattern into LoopController/MidiHub; the engine renders in
real time on a thread at volume 0. Checks: hits land where they were played, the loop
length and tempo, an assigned transport button, save/load, WAV and MIDI export.

    PYTHONPATH=. python tests/live_loop.py
"""

import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="chimp-loop-test-"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
os.environ["XDG_DATA_HOME"] = str(tmp / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import rtmidi  # noqa: E402
from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from chimp.loop import files  # noqa: E402
from chimp.loop.controller import LoopController  # noqa: E402
from chimp.loop.engine import Engine  # noqa: E402

files.EXPORT_DIR = tmp / "export"
RATE, BLOCK = 48000, 256
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


app = QCoreApplication(sys.argv)
e = Engine(RATE)
ctl = LoopController(e)
e.volume = 0.0
e.quantise = 0
e.metronome = False

stop = threading.Event()


def audio() -> None:  # a sound card that plays exactly in real time
    nxt = time.monotonic()
    while not stop.is_set():
        e.render(BLOCK)
        nxt += BLOCK / RATE
        time.sleep(max(0.0, nxt - time.monotonic()))


threading.Thread(target=audio, daemon=True).start()
port = rtmidi.MidiOut(rtmidi.API_LINUX_ALSA, name="Test Pads")
port.open_virtual_port("Test Pads")
ctl.start()

played: list[float] = []


def note(n: int, vel: int = 100) -> None:
    played.append(time.monotonic())
    port.send_message([0x90, n, vel])
    port.send_message([0x80, n, 0])


def script() -> None:
    check("Test Pads" in ctl.devices, f"virtual controller seen ({ctl.devices})")
    t0 = time.monotonic()
    ctl.runAction("record")
    for i, n in enumerate((36, 37, 36, 37)):  # pads 1 2 1 2 = kick snare kick snare, every 250 ms
        time.sleep(max(0, t0 + 0.05 + i * 0.25 - time.monotonic()))
        note(n)
    time.sleep(max(0, t0 + 1.0 - time.monotonic()))
    ctl.runAction("record")  # close: 1 s loop


def after_take() -> None:
    check(e.state == "playing", f"state after closing the take: {e.state}")
    check(abs(e.length / RATE - 1.0) < 0.02, f"loop length {e.length / RATE:.3f} s (wanted 1.0)")
    check(abs(e.bpm - 120.0) < 3, f"tempo guessed {e.bpm:.1f} (wanted ~120 for 2 beats)")
    hits = sorted(e.hits(), key=lambda h: h.t)
    check([h.sound for h in hits] == [0, 1, 0, 1], f"sounds {[h.sound for h in hits]}")
    pos_ms = [h.t / RATE * 1000 for h in hits]
    want = [50, 300, 550, 800]
    err = [abs(a - b) for a, b in zip(pos_ms, want, strict=False)]
    check(
        max(err, default=99) < 12,
        f"hit positions {[round(p) for p in pos_ms]} ms (want {want}, max err {max(err, default=0):.1f})",
    )
    check(abs(hits[0].vel - 100 / 127) < 0.01, "velocity kept")
    # assign a CC button to play/stop and use it
    ctl.assign("action:play")
    port.send_message([0xB0, 20, 127])


def after_assign() -> None:
    check(ctl.s.assign.get("Test Pads", {}).get("cc:1:20") == "play", f"CC 20 learned as play/stop ({ctl.s.assign})")
    port.send_message([0xB0, 20, 127])


def after_button() -> None:
    check(e.state == "stopped", f"assigned button stopped playback: {e.state}")
    ctl.saveLoop("test loop")
    check("test loop" in ctl.savedLoops, "saved")
    before = [(h.t, h.sound) for h in e.hits()]
    e.clear()
    ctl.loadLoop("test loop")
    after = [(h.t, h.sound) for h in e.hits()]
    check(after == before and e.state == "stopped", "loaded back identical")
    wav = ctl.exportWav("test loop", 2)
    with wave.open(wav, "rb") as w:
        secs = w.getnframes() / w.getframerate()
    check(abs(secs - 2.0) < 0.01, f"WAV is 2 loops long ({secs:.3f} s)")
    mid = Path(ctl.exportMidi("test loop")).read_bytes()
    check(
        mid[:4] == b"MThd" and mid.count(b"\x99\x24") == 2 and mid.count(b"\x99\x26") == 2,
        "MIDI file: 2 kicks, 2 snares on ch 10",
    )
    # knobs: CC 70-77 follow the knob page and act at once (MIDI thread)
    ctl.setKnobPage("master")
    port.send_message([0xB0, 73, 64])  # knob 4 on the master page: delay
    port.send_message([0xB0, 70, 0])  # knob 1: filter all the way down


def after_knobs() -> None:
    check(abs(e.master.delay.mix - 64 / 127) < 1e-6, f"CC 73 turned the master delay ({e.master.delay.mix:.2f})")
    texts = {p["id"]: p["text"] for p in ctl.masterParams}
    check(
        e.master.filter_value == 0.0 and texts["m.filter"] == "LP 80 Hz",
        f"CC 70 turned the filter ({texts['m.filter']})",
    )
    ctl.setKnobPage("pad")
    ctl.selectPad(1)
    port.send_message([0xB0, 70, 32])  # knob 1 on the pad page: the selected pad's level


def after_pad_knob() -> None:
    check(
        abs(e.pads[1]["level"] - 32 / 127) < 1e-6 and e.pads[0]["level"] == 0.8,
        "pad page: knob 1 = selected pad's level",
    )
    ctl.assign("param:m.reverb")
    port.send_message([0xB0, 21, 5])


def after_learn() -> None:
    check(ctl.s.assign.get("Test Pads", {}).get("cc:1:21") == "param:m.reverb", "CC 21 learned for master reverb")
    port.send_message([0xB0, 21, 127])


def after_learned_knob() -> None:
    check(e.master.reverb.mix == 1.0, f"learned knob turns reverb ({e.master.reverb.mix})")
    # step entry through the controller's slots
    ctl.runAction("clear")
    ctl.toggleStep(0, 0)
    ctl.toggleStep(1, 4)
    ctl.cycleStepVelocity(1, 4)
    cells = ctl.gridCells
    check(cells[0][0] == 0.8 and cells[1][4] == 1.0 and e.state == "stopped", "step entry and accent")
    # hold actions on controller buttons: roll, select, a stutter
    for cc, action in ((30, "roll"), (31, "select"), (32, "g.stutter16")):
        ctl.assign("action:" + action)
        ctl._input_ui("Test Pads", "press", f"cc:1:{cc}", 1.0)
    check(ctl.s.target("Test Pads", "cc:1:32") == "g.stutter16", "hold actions learned")
    ctl.runAction("play")  # the 1-bar step pattern plays
    ctl.runAction("record")  # overdub
    port.send_message([0xB0, 30, 127])  # roll down
    port.send_message([0x90, 38, 100])  # pad 3 (clap) held


def after_roll_held() -> None:
    port.send_message([0x80, 38, 0])
    port.send_message([0xB0, 30, 0])  # roll up
    claps = [h for h in e.hits() if h.sound == 2]
    check(len(claps) >= 3, f"roll repeated and recorded while held ({len(claps)} claps)")
    sel_before = e.selected
    port.send_message([0xB0, 31, 127])  # select down
    port.send_message([0x90, 41, 100])  # pad 6
    port.send_message([0x80, 41, 0])
    port.send_message([0xB0, 31, 0])
    check(sel_before != 5, "precondition")


def after_select() -> None:
    check(not e.rolling(), "roll stops on release")
    check(e.selected == 5, f"select + pad chose part 6 ({e.selected + 1})")
    port.send_message([0x90, 36, 100])  # playing a pad doesn't move the selection
    port.send_message([0xB0, 32, 127])  # stutter down


def after_stutter_down() -> None:
    check(e.selected == 5, "playing pad 1 kept part 6 selected")
    check(e.master.glitch.mode == "stutter16", f"stutter engaged ({e.master.glitch.mode!r})")
    port.send_message([0xB0, 32, 0])


def after_stutter_up() -> None:
    check(e.master.glitch.mode == "", "stutter released")
    # a gamepad trigger (as the Wooting's analog keys arrive) learned onto the master drive
    ctl.assign("param:m.drive")
    ctl._input_ui("Generic X-Box pad", "value", "gpa:ABS_RZ", 0.05)  # resting: not learned
    still = ctl.assigning == "param:m.drive"
    ctl._input_ui("Generic X-Box pad", "value", "gpa:ABS_RZ", 0.6)
    ctl._input_now("Generic X-Box pad", "value", "gpa:ABS_RZ", 0.8)
    check(still and abs(e.master_settings["drive"] - 0.8) < 1e-6, "gamepad trigger learned and turns drive")
    stop.set()
    ctl.stop()
    app.quit()


def chain(*steps) -> None:
    """Run each step, then the next 300 ms later (letting MIDI messages arrive)."""
    if steps:
        steps[0]()
        QTimer.singleShot(300, lambda: chain(*steps[1:]))


STEPS = [
    script,
    after_take,
    after_assign,
    after_button,
    after_knobs,
    after_pad_knob,
    after_learn,
    after_learned_knob,
    lambda: None,  # let the roll play a few sixteenths (300 ms each step)
    lambda: None,
    lambda: None,
    after_roll_held,
    after_select,
    after_stutter_down,
    after_stutter_up,
]
QTimer.singleShot(2000, lambda: chain(*STEPS))
QTimer.singleShot(20000, app.quit)
app.exec()
print("FAILED" if failures else "all good", f"({tmp})")
sys.exit(1 if failures else 0)
