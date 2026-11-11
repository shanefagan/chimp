"""The Loop window's view of the engine: properties for QML, slots for buttons and keys,
and the MIDI path (pads -> sounds and transport, knobs -> pad or master, assign mode)."""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Property, QFileSystemWatcher, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ..desktop.midi import MidiHub
from ..desktop.service import STATUS_PATH
from ..device import Lpd8Device
from . import dsp, files, layout, voice
from . import kit as kits
from .engine import (
    KNOB_PAGE_LABELS,
    KNOB_PAGES,
    MASTER_CHOICES,
    MASTER_KNOBS,
    MAX_SWING,
    PRESETS,
    QUANTISE_STEPS,
    STEPS_PER_BAR,
    Engine,
    master_defaults,
)
from .fx import DELAY_TIMES, GLITCHES, REVERB_MODES
from .gamepad import Gamepads
from .kit import NUM_SOUNDS, kit_names
from .kit import load as load_kit
from .mapping import ACTIONS, HOLD_ACTIONS, LoopSettings, describe, key_of, release_of, value_of

QUANTISE_LABELS = {0: "Off", 4: "1/4", 8: "1/8", 16: "1/16", 32: "1/32"}
ROLL_RATES = [(8, "1/8"), (16, "1/16"), (24, "1/16T"), (32, "1/32")]
CHOICE_LABELS = {
    "filter_type": {"lp": "Low-pass", "hp": "High-pass", "bp": "Band-pass"},
    "drive_type": {"soft": "Soft", "hard": "Hard clip", "fuzz": "Fuzz", "crush": "Crush"},
    "reverb_mode": dict(REVERB_MODES),
    "delay_time": {i: name for i, (name, _beats) in enumerate(DELAY_TIMES)},
    "comp_speed": {"slow": "Slow (glue)", "fast": "Fast (punch)"},
}


def _hz(hz: float) -> str:
    return f"{hz / 1000:.1f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"


def _secs(s: float) -> str:
    return f"{s * 1000:.0f} ms" if s < 1 else f"{s:.1f} s"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


class LoopController(QObject):
    changed = Signal()  # settings, kit, devices, assignments, saved loops, grid
    tick = Signal()  # 30 times a second: playhead, state
    padHit = Signal(int, float)  # sound, velocity: for the pad flash
    notify = Signal(str)

    def __init__(self, engine: Engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.e = engine
        self.s = LoopSettings.load()
        self._kits = kit_names()
        self._kit_name = ""
        self._assigning = ""  # "sound:N", "action:name" or "param:p.x" while waiting for a control
        self._taps: list[float] = []
        self._latency_text = "starting audio…"
        self._last_rev = -1
        self._page = 0
        self._follow = True  # the grid page follows the playhead
        self._apply_settings()
        self._held: set[str] = set()  # hold actions down right now (roll, select, mute, solo, g.*)
        # presses a held select/mute/solo already handled on the input thread: the UI skips them
        self._consumed: set[tuple[str, str]] = set()
        self._sound_name = ""
        self.hub = MidiHub("Chimp Loop", realtime=self._on_midi_now)
        self.hub.message.connect(self._on_midi_ui)
        self.hub.devicesChanged.connect(self._on_devices)
        self.pads = Gamepads(self._input_now)
        self.pads.inputEvent.connect(self._input_ui)
        self.pads.devicesChanged.connect(self.changed)
        self._timer = QTimer(self, interval=33, timeout=self._on_tick)
        # The LPD8 on one of its desktop programs belongs to the desktop service: its knobs
        # (CC 70-77 on every program, out of the box) mustn't also turn the looper's.
        self._lpd8_desktop = 0  # that program's number, or 0
        self._status_mtime = 0.0
        self._status_pid = 0  # the desktop service, while its status file is fresh
        self._status_active = 0  # the LPD8 program it last saw
        self._status_programs: set[int] = set()  # the programs it uses for the desktop
        self._desktop_programs: set[int] = set()
        self._status_seen: tuple = (set(), 0)
        # The LPD8's programs, and which one it's on, read from the device itself (the
        # programs all send the same notes out of the box: only the device can say).
        self.lpd8 = Lpd8Device(self, client_name="Chimp Loop")
        self.lpd8.connectionChanged.connect(self._on_lpd8_connection)
        self.lpd8.programReceived.connect(self._on_lpd8_program)
        self.lpd8.activeProgramReceived.connect(self._on_lpd8_active)
        self._lpd8_programs: dict = {}
        self._lpd8_active = 0  # 0 = not known
        self._lpd8_poll = QTimer(self, interval=350, timeout=self._poll_lpd8)
        # What Chimp knows each connected controller's pads and knobs do (layout.py).
        # Replaced whole, never changed in place: the MIDI thread reads it.
        self._tables: dict[str, dict[str, str]] = {}
        # The pads on screen: shaped like the controller in your hands, a page at a time.
        self._hand = ""  # that controller: the known one whose pads were hit last
        self._shape = layout.Shape()
        self._view = 0  # the page of sounds showing
        self._view_fx = False  # ...or the FX page (rolls and glitches) instead
        self._status_timer = QTimer(self, interval=400, timeout=self._poll_status)
        # Knob sweeps change parameters many times a second: save once they settle.
        self._save_later = QTimer(self, singleShot=True, interval=1500, timeout=self._save_settings)
        # Kit folders added or removed by hand show up in the list without a restart.
        self._kit_watch = QFileSystemWatcher(self)
        self._kit_watch.directoryChanged.connect(lambda _path: self._rescan_later.start())
        self._rescan_later = QTimer(self, singleShot=True, interval=600, timeout=self._rescan_kits)
        self._watch_kits()

    def start(self) -> None:
        self._poll_status()
        self._reshape()
        self.hub.start()
        self.lpd8.start()
        self._lpd8_poll.start()
        self.pads.start()
        self._timer.start()
        self._status_timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._lpd8_poll.stop()
        self.lpd8.stop()
        self.hub.stop()
        self.pads.stop()
        self._save_settings()
        self.e.close()

    def _apply_settings(self) -> None:
        s = self.s
        e = self.e
        e.bpm, e.bars, e.metronome = float(s.bpm), int(s.bars), bool(s.metronome)
        e.quantise = s.quantise if s.quantise in QUANTISE_STEPS else 16
        e.fixed_velocity = bool(s.fixed_velocity)
        e.knob_page = s.knob_page if s.knob_page in KNOB_PAGES else "pad"
        if s.sound:
            e.apply_sound_settings(s.sound)
        self._load_kit(self._kits.index(s.kit) if s.kit in self._kits else 0)

    def _load_kit(self, index: int) -> None:
        if not self._kits:
            self.notify.emit("No drum kits found")
            return
        name = self._kits[index % len(self._kits)]
        try:
            kit = load_kit(name, self.e.rate)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't load kit {name}: {exc}")
            return
        self.e.set_kit(kit)
        self._kit_name = name
        self.s.kit = name

    # --- kits on disk -------------------------------------------------------------------

    def _watch_kits(self) -> None:
        with contextlib.suppress(OSError):
            kits.USER_DIR.mkdir(parents=True, exist_ok=True)
        want = [str(kits.USER_DIR)] + [str(d) for d in kits.kit_dirs()]
        have = self._kit_watch.directories()
        if gone := [d for d in have if d not in want]:
            self._kit_watch.removePaths(gone)
        if new := [d for d in want if d not in have and Path(d).is_dir()]:
            self._kit_watch.addPaths(new)

    def _rescan_kits(self, select: str = "") -> None:
        """Re-read the kit list; `select` loads that kit, else the current one stays."""
        self._watch_kits()
        names = kit_names()
        if select in names:
            self._kits = names
            self._load_kit(names.index(select))
            self._save_settings()
        elif names != self._kits:
            self._kits = names
            if self._kit_name not in names:  # its folder went away
                self._load_kit(0)
                self._save_settings()
        else:
            return
        self.changed.emit()

    @staticmethod
    def _path(url: QUrl | str) -> Path:
        u = QUrl(url)
        return Path(u.toLocalFile() if u.isLocalFile() else str(url))

    @Property(str, constant=True)
    def kitsFolder(self) -> str:
        return str(kits.USER_DIR)

    @Property(bool, notify=changed)
    def kitIsBuiltIn(self) -> bool:
        return self._kit_name == kits.BUILTIN

    @Slot()
    def openKitsFolder(self) -> None:
        self._watch_kits()
        target = kits.USER_DIR / self._kit_name if self._kit_name != kits.BUILTIN else kits.USER_DIR
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target if target.is_dir() else kits.USER_DIR)))

    @Slot("QVariant")
    def importKit(self, url) -> None:
        """A folder of WAVs, a .chimpkit or a .zip becomes one of your kits, and is selected."""
        src = self._path(url)
        try:
            name = kits.import_kit(src)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't import {src.name}: {exc}")
            return
        self._rescan_kits(select=name)
        n = sum(1 for s in self.e.kit.sounds if s) if self.e.kit else 0
        self.notify.emit(f"Imported kit {name} ({n} sounds)")

    @Slot(str)
    def newKit(self, name: str) -> None:
        """An empty kit to fill pad by pad (Sample…, or drop WAVs on the pads)."""
        try:
            kit = kits.new_kit(name.strip() or "new kit")
        except OSError as exc:
            self.notify.emit(f"Couldn't make a kit: {exc}")
            return
        self._rescan_kits(select=kit)
        self.notify.emit(f"New kit {kit}: drop WAVs on the pads, or Sample… on a pad")

    @Property(str, notify=changed)
    def kitName(self) -> str:
        return self._kit_name

    @Property(QUrl, notify=changed)
    def kitExportUrl(self) -> QUrl:
        """Where Export suggests saving the current kit."""
        with contextlib.suppress(OSError):
            files.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        return QUrl.fromLocalFile(str(files.EXPORT_DIR / f"{self._kit_name}{kits.EXTENSION}"))

    @Slot("QVariant")
    def exportKit(self, url) -> None:
        dest = self._path(url)
        try:
            out = kits.export_kit(self._kit_name, dest, self.e.rate)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't export {self._kit_name}: {exc}")
            return
        self.notify.emit(f"Saved {out.name}")

    def _edit(self, what: str, fn) -> bool:
        """Run a kit edit (fn(kit) -> kit it went into), reload, say where it went."""
        was = self._kit_name
        try:
            name = fn(was)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't {what}: {exc}")
            return False
        self._rescan_kits(select=name)
        if name != self._kit_name:  # the edited kit wouldn't load: _load_kit said why
            return False
        if name != was:
            self.notify.emit(f"Editing {name}, a copy of {was} (the built-in kit stays as it is)")
        return True

    @Slot("QVariant")
    def replaceSample(self, url) -> None:
        self.replacePadSample(self.e.selected, url)

    @Slot(int, "QVariant")
    def replacePadSample(self, pad: int, url) -> None:
        """Put a WAV on a pad. The built-in kit is copied to an editable kit of yours first."""
        src = self._path(url)
        copying = self.kitIsBuiltIn
        if self._edit(f"use {src.name}", lambda k: kits.replace_sample(k, pad, src, self.e.rate)) and not copying:
            self.notify.emit(f"{src.stem} → pad {pad + 1}")

    @Slot(int, str)
    def renamePad(self, pad: int, label: str) -> None:
        if label.strip():
            self._edit("rename the pad", lambda k: kits.rename_pad(k, pad, label, self.e.rate))

    @Slot(int)
    def clearPad(self, pad: int) -> None:
        self._edit("clear the pad", lambda k: kits.clear_pad(k, pad, self.e.rate))

    @Slot("QVariant")
    def dropped(self, urls) -> None:
        """Files dropped on the window: kits import, a WAV goes on the selected pad."""
        self.droppedOnPad(self.e.selected, urls)

    @Slot(int, "QVariant")
    def droppedOnPad(self, pad: int, urls) -> None:
        """Folders, .chimpkit and .zip files import as kits; otherwise the first WAV goes on `pad`."""
        paths = [self._path(u) for u in (urls or [])]
        packs = [p for p in paths if p.is_dir() or (p.is_file() and p.suffix.lower() in (kits.EXTENSION, ".zip"))]
        wavs = [p for p in paths if p.is_file() and p.suffix.lower() == ".wav"]
        for f in packs:
            self.importKit(str(f))
        if wavs and not packs:
            self.replacePadSample(pad, str(wavs[0]))
        elif not packs:
            self.notify.emit(f"Drop a kit (a folder of WAVs, a {kits.EXTENSION} or a .zip) or a WAV for a pad")

    def _save_settings(self) -> None:
        with self.e.lock:
            self.s.bpm, self.s.bars, self.s.quantise = self.e.bpm, self.e.bars, self.e.quantise
            self.s.metronome, self.s.volume = self.e.metronome, self.e.volume
            self.s.fixed_velocity, self.s.knob_page = self.e.fixed_velocity, self.e.knob_page
            self.s.sound = self.e.sound_settings()
        try:
            self.s.save()
        except OSError as exc:
            self.notify.emit(f"Couldn't save settings: {exc}")

    # --- the desktop service's LPD8 program ---------------------------------------------

    def _poll_status(self) -> None:
        try:
            mtime = STATUS_PATH.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._status_mtime:
            self._status_mtime = mtime
            pid, active, desktop = 0, 0, set()
            with contextlib.suppress(OSError, ValueError, TypeError):
                st = json.loads(STATUS_PATH.read_text())
                pid = int(st.get("pid", 0))
                if st.get("connected"):
                    active = int(st.get("active") or 0)
                desktop = {int(n) for n in st.get("desktopPrograms") or []}
            self._status_pid, self._status_active, self._status_programs = pid, active, desktop
        # the file outlives a service that was killed: its programs are only the desktop's
        # while it runs
        pid = self._status_pid
        alive = pid > 0 and pid != os.getpid() and Path(f"/proc/{pid}").exists()
        desktop = self._status_programs if alive else set()
        seen = (desktop, self._status_active if alive else 0)
        if seen != self._status_seen:
            self._status_seen = seen
            self._desktop_programs = desktop
            self._update_lpd8()

    @Property(str, notify=changed)
    def lpd8Note(self) -> str:
        n = self._lpd8_desktop
        return f"LPD8 on Program {n} = desktop control (switch program to drum)" if n else ""

    def _ignored(self, device: str) -> bool:
        return bool(self._lpd8_desktop) and device == layout.LPD8

    # --- the LPD8's programs, and the controller in your hands ---------------------------

    def _on_lpd8_connection(self, connected: bool, _port: str) -> None:
        self._lpd8_programs, self._lpd8_active = {}, 0
        if connected:
            for slot in range(1, 5):
                self.lpd8.request_program(slot)
            self.lpd8.request_active_program()
        self._update_lpd8()

    def _poll_lpd8(self) -> None:
        if self.lpd8.connected:
            self.lpd8.request_active_program()

    def _on_lpd8_program(self, slot: int, program) -> None:
        self._lpd8_programs[slot] = program
        if slot == self._active_program():
            self._update_lpd8()

    def _on_lpd8_active(self, slot: int) -> None:
        if slot == self._lpd8_active:
            return
        switched = self._lpd8_active != 0
        self._lpd8_active = slot
        self.lpd8.request_program(slot)  # it may have been edited since it was read
        self._update_lpd8(switched=switched)

    def _active_program(self) -> int:
        """The program the LPD8 is on: from the device, or failing that the desktop service."""
        return self._lpd8_active or self._status_active

    def _lpd8_job(self) -> str:
        """What the LPD8's pads do on its current program: "A", "B"..., "fx", or "" when
        that isn't known or the program is the desktop's."""
        active = self._active_program()
        if not active or active in self._desktop_programs or layout.LPD8 not in self.hub.devices:
            return ""
        return layout.lpd8_jobs(self._desktop_programs, self.s.lpd8_jobs).get(active, "")

    def _update_lpd8(self, switched: bool = False) -> None:
        """The LPD8's program, or what's known about it, changed: redo what its pads do
        and show its page."""
        active = self._active_program()
        desktop = active if active in self._desktop_programs else 0
        if desktop != self._lpd8_desktop:
            self._lpd8_desktop = desktop
            if desktop:
                self.notify.emit(f"LPD8 is on Program {desktop} (desktop control): switch program to play drums")
        job = self._lpd8_job()
        before = self._tables.get(layout.LPD8)
        self._rebuild_tables()
        if self._tables.get(layout.LPD8) != before:
            self._release_holds()  # a pad held across the switch would never be let go
        if job and (switched or not self._hand):
            self._hand = layout.LPD8
        if job and self._hand == layout.LPD8:
            self._reshape()
            if job == layout.FX:
                self._view_fx = True
            else:
                self._view_fx = False
                self._view = min(layout.job_page(job), layout.page_count(self._shape) - 1)
            if switched:
                # the knobs follow: the selected pad's sound on a page of pads, the master on FX
                on_master = self.e.knob_page.startswith("master")
                if (job == layout.FX) != on_master:
                    self.e.knob_page = "master" if job == layout.FX else "pad"
                self.notify.emit(f"LPD8 Program {active}: {self._job_text(job)}")
        self.e._changed()
        self.changed.emit()

    def _job_text(self, job: str) -> str:
        if job == layout.FX:
            return "FX pads (roll and glitches), knobs on the master"
        sounds = layout.page_sounds(self._shape, layout.job_page(job))
        return f"page {job}, sounds {sounds[0] + 1}–{sounds[-1] + 1}" if sounds else f"page {job}"

    def _release_holds(self) -> None:
        for name in list(self._held):
            self._hold(name, False)
        for sound in self.e.rolling():
            self.e.roll_stop(sound)

    def _rebuild_tables(self) -> None:
        tables: dict[str, dict[str, str]] = {}
        for device in self.hub.devices:
            if device == layout.LPD8:
                job = self._lpd8_job()
                program = self._lpd8_programs.get(self._active_program())
                if job and program is not None:
                    tables[device] = layout.lpd8_table(program, job)
            else:
                known = layout.controllers.find(device)
                if known is not None:
                    tables[device] = layout.table_for(known)
        self._tables = tables

    def _on_devices(self) -> None:
        if self._hand and self._hand not in self.hub.devices:
            self._hand = ""
        if not self._hand:  # nothing in your hands yet: the first controller Chimp knows
            self._hand = next((d for d in self.hub.devices if layout.shape_of(d)), "")
        self._reshape()
        self._update_lpd8()

    def _reshape(self) -> bool:
        """Fit the pads on screen to the controller in hand (or the size you chose)."""
        if self.s.pads_shown in layout.SIZES:
            shape = layout.forced(self.s.pads_shown)
        else:
            shape = (layout.shape_of(self._hand) if self._hand else None) or layout.Shape()
        if shape == self._shape:
            return False
        first = self._view * self._shape.size  # keep the same sounds in view
        self._shape = shape
        self._view = min(first // shape.size, layout.page_count(shape) - 1)
        return True

    def _played(self, device: str, target: str) -> None:
        """A pad was hit on a controller: it's the one in your hands now, and the page on
        screen is the one it's playing."""
        moved = False
        if device != self._hand and layout.shape_of(device):
            self._hand = device
            moved = self._reshape()
        if device != self._hand and self._hand:
            return  # some other controller: the pads stay with the one in hand
        if target.startswith("sound:"):
            page = int(target[6:]) // self._shape.size
            if page != self._view or self._view_fx:
                self._view, self._view_fx, moved = page, False, True
        elif device == layout.LPD8 and self._lpd8_job() == layout.FX and not self._view_fx:
            self._view_fx = moved = True
        if moved:
            self.changed.emit()

    @Property(int, notify=changed)
    def padColumns(self) -> int:
        return self._shape.columns

    @Property(int, notify=changed)
    def padRows(self) -> int:
        return self._shape.rows

    @Property("QVariantList", notify=changed)
    def padCells(self) -> list:
        """The pads on screen in drawing order (top row first): a sound, an FX pad
        (roll or a glitch, held), or a gap."""
        gap = {"sound": -1, "fx": "", "label": ""}
        if self._view_fx:
            names = dict(ACTIONS)
            pads = layout.FX_PADS[: self._shape.size]
            return [
                {
                    "sound": -1,
                    "fx": pads[i],
                    "label": names[pads[i]].removeprefix("Hold: ").replace(" (+ pad)", "").capitalize(),
                }
                if i >= 0
                else gap
                for i in layout.cells(self._shape, len(pads))
            ]
        sounds = layout.page_sounds(self._shape, self._view)
        return [
            {"sound": sounds[i], "fx": "", "label": ""} if i >= 0 else gap
            for i in layout.cells(self._shape, len(sounds))
        ]

    @Property("QVariantList", notify=changed)
    def stepSounds(self) -> list:
        """The step grid's rows: the page of sounds on screen."""
        return layout.page_sounds(self._shape, self._view)

    @Property("QVariantList", notify=changed)
    def pageTabs(self) -> list:
        """The pages to switch between; `here` marks the one the controller is on."""
        job = self._lpd8_job() if self._hand == layout.LPD8 else ""
        tabs = []
        for i in range(layout.page_count(self._shape)):
            sounds = layout.page_sounds(self._shape, i)
            name = layout.page_name(i)
            tabs.append(
                {"id": str(i), "label": name, "detail": f"{sounds[0] + 1}–{sounds[-1] + 1}", "here": job == name}
            )
        if job or self._view_fx:
            tabs.append({"id": layout.FX, "label": "FX", "detail": "roll, glitches", "here": job == layout.FX})
        return tabs if len(tabs) > 1 else []

    @Property(str, notify=changed)
    def viewPage(self) -> str:
        return layout.FX if self._view_fx else str(self._view)

    @Slot(str)
    def setViewPage(self, page: str) -> None:
        """Look at another page. The controller keeps playing the one it's on, and the
        view goes back there with its next pad."""
        if page == layout.FX:
            self._view_fx = True
        elif page.isdigit() and int(page) < layout.page_count(self._shape):
            self._view, self._view_fx = int(page), False
        self.changed.emit()

    @Property(str, notify=changed)
    def handNote(self) -> str:
        """Whose pads these are."""
        if self.s.pads_shown in layout.SIZES or not self._hand:
            return ""
        return self._shape.name or self._hand  # (the LPD8's program is in lpd8Programs)

    @Property("QVariantList", notify=changed)
    def lpd8Programs(self) -> list:
        """What each LPD8 program does here, for the hint under the pads."""
        if layout.LPD8 not in self.hub.devices:
            return []
        jobs = layout.lpd8_jobs(self._desktop_programs, self.s.lpd8_jobs)
        out = []
        for slot in range(1, 5):
            job = jobs.get(slot, "")
            text = "desktop" if not job else "FX" if job == layout.FX else f"page {job}"
            out.append({"program": slot, "text": text, "here": slot == self._active_program()})
        return out

    @Property(int, notify=changed)
    def padsShown(self) -> int:
        return self.s.pads_shown if self.s.pads_shown in layout.SIZES else 0

    @Slot(int)
    def setPadsShown(self, n: int) -> None:
        self.s.pads_shown = n if n in layout.SIZES else 0
        self._reshape()
        self._save_settings()
        self.changed.emit()

    @Property(int, notify=changed)
    def knobCount(self) -> int:
        """How many knobs the controller in hand has for the knob page (8 at most)."""
        shape = layout.shape_of(self._hand) if self._hand else None
        return shape.knobs if shape else layout.MAX_KNOBS

    @Property("QVariantList", notify=changed)
    def knobPages(self) -> list:
        return [{"id": k, "label": KNOB_PAGE_LABELS[k]} for k in KNOB_PAGES]

    def _target(self, device: str, key: str) -> str | None:
        return self.s.target(device, key, self._tables.get(device))

    # --- MIDI ------------------------------------------------------------------------

    def _param_of(self, target: str | None) -> str | None:
        """The parameter a knob target turns right now (None: not a knob, or a knob with
        nothing on this knob page)."""
        if not target:
            return None
        if target.startswith("knob:"):
            return self.e.knob_target(int(target[5:]))
        if target.startswith("param:"):
            return target[6:]
        return None

    @staticmethod
    def _is_knob(target: str | None) -> bool:
        return bool(target) and target.startswith(("knob:", "param:"))

    @staticmethod
    def _events(msg: list[int]) -> list[tuple[str, str, float]]:
        """A MIDI message as (kind, key, value) inputs: a value (knob position), a press
        (with velocity), a release."""
        out = []
        v = value_of(msg)
        if v is not None:
            out.append(("value", v[0], v[1]))
        k = key_of(msg)
        if k is not None:
            out.append(("press", k[0], k[1]))
        r = release_of(msg)
        if r is not None:
            out.append(("release", r, 0.0))
        return out

    def _on_midi_now(self, device: str, msg: list[int]) -> None:
        for kind, key, value in self._events(msg):
            if self._input_now(device, kind, key, value):
                return

    def _on_midi_ui(self, device: str, msg: list[int]) -> None:
        for kind, key, value in self._events(msg):
            if self._input_ui(device, kind, key, value):
                return

    def _input_now(self, device: str, kind: str, key: str, value: float) -> bool:
        """MIDI/gamepad thread: anything timing-critical acts here at once (sounds,
        knobs, rolls, glitches). True if the input was a knob (so a CC isn't also a press)."""
        if self._assigning or self._ignored(device):
            return False
        target = self._target(device, key)
        if kind == "value":
            pid = self._param_of(target)
            if pid:
                self.e.set_param(pid, value)
            return self._is_knob(target)
        if not target or self._is_knob(target):
            return False
        if target.startswith("sound:"):
            sound = int(target[6:])
            if kind == "press":
                if self._modify(sound):  # decided now, while the modifier is surely down
                    self._consumed.add((device, key))
                    return False
                if "roll" in self._held:
                    self.e.roll_start(sound, value, time.monotonic())
                else:
                    self.e.trigger(sound, value, time.monotonic())
            else:
                self.e.roll_stop(sound)
        elif target in HOLD_ACTIONS:
            self._hold(target, kind == "press")
        return False

    def _input_ui(self, device: str, kind: str, key: str, value: float) -> bool:
        if self._ignored(device):
            return False
        if self._assigning:
            if self._assigning.startswith("param:"):
                if kind == "value" and self._moved(key, value):
                    self._learn(device, key)
                    return True
            elif kind == "press":
                self._learn(device, key)
                return True
            return False
        target = self._target(device, key)
        if kind == "value":
            return self._is_knob(target)
        if not target or self._is_knob(target):
            return False
        if kind == "press" and (target.startswith("sound:") or target in HOLD_ACTIONS):
            self._played(device, target)
        if target.startswith("sound:") and kind == "press":
            if (device, key) in self._consumed:
                self._consumed.discard((device, key))
                self.changed.emit()
            else:
                self.padHit.emit(int(target[6:]), value)
        elif target in HOLD_ACTIONS:
            self.changed.emit()
        elif kind == "press":
            self.runAction(target)
        return False

    @staticmethod
    def _moved(key: str, value: float) -> bool:
        """Clearly moved, not resting jitter: triggers rest at 0, sticks in the middle."""
        if not key.startswith("gpa:"):
            return True
        if key[4:] in ("ABS_Z", "ABS_RZ", "ABS_GAS", "ABS_BRAKE", "ABS_THROTTLE"):
            return value > 0.3
        return abs(value - 0.5) > 0.25

    def _hold(self, name: str, down: bool) -> None:
        if down:
            self._held.add(name)
            if name.startswith("g."):
                self.e.glitch(name[2:])
        else:
            self._held.discard(name)
            if name.startswith("g."):
                self.e.glitch_off(name[2:])
            elif name == "roll":
                for sound in self.e.rolling():
                    self.e.roll_stop(sound)

    def _modify(self, sound: int) -> bool:
        """A pad hit while select/mute/solo is held: do that instead of playing."""
        if "select" in self._held:
            self.e.select(sound)
        elif "mute" in self._held:
            self.e.toggle_mute(sound)
        elif "solo" in self._held:
            self.e.toggle_solo(sound)
        else:
            return False
        return True

    def _learn(self, device: str, key: str) -> None:
        what = self._assigning
        self.s.set_assign(device, key, what)
        self._assigning = ""
        if what.startswith("action:"):
            label = ACTIONS.get(what[7:], what)
        elif what.startswith("param:"):
            label = self._label(what[6:])
        else:
            label = self.padLabels[int(what[6:])]
        self.notify.emit(f"{label} ← {device} · {describe(key)}")
        self._save_settings()
        self.changed.emit()

    def _label(self, pid: str) -> str:
        scope, key = pid.split(".", 1)
        if scope == "m":
            return "Master " + MASTER_KNOBS[key][0].lower()
        return f"Pad {voice.PAD_KNOBS[key][0].lower()}"

    # --- ticking ------------------------------------------------------------------------

    def _on_tick(self) -> None:
        e = self.e
        if self._follow and e.state in ("playing", "overdub") and e.length:
            page = int(e.step_of(e.playhead) // STEPS_PER_BAR)
            if page != self._page and page < self.gridPages:
                self._page = page
                self.changed.emit()
        self.tick.emit()
        if e.revision != self._last_rev:
            self._last_rev = e.revision
            self.changed.emit()
            self._save_later.start()  # knob moves from the controller land here too

    # --- transport and loop properties ---------------------------------------------------

    @Property(str, notify=tick)
    def state(self) -> str:
        return self.e.state

    @Property(str, notify=tick)
    def timeText(self) -> str:
        e = self.e
        r = float(e.rate)
        if e.state == "recording":
            return f"{max(0, e.playhead) / r:4.1f} s"
        if not e.length:
            return ""
        return f"{e.length / r:.2f} s · {e.steps() // STEPS_PER_BAR or 1} bar{'s' if e.steps() > 16 else ''}"

    @Property(int, notify=tick)
    def stepNow(self) -> int:
        """The playing step within the shown page, or -1."""
        e = self.e
        if e.state not in ("playing", "overdub") or not e.length:
            return -1
        k = int(e.playhead / e.step_frames()) % e.steps() - self._page * STEPS_PER_BAR
        return k if 0 <= k < STEPS_PER_BAR else -1

    @Property(float, notify=tick)
    def recordProgress(self) -> float:
        e = self.e
        if e.state == "recording" and e.bars:
            return max(0.0, e.playhead / (e.bars * e.bar_frames()))
        return -1.0

    @Property(float, notify=changed)
    def bpm(self) -> float:
        return round(self.e.bpm, 1)

    @Property(int, notify=changed)
    def bars(self) -> int:
        return self.e.bars

    @Property(int, notify=changed)
    def quantise(self) -> int:
        return self.e.quantise

    @Property(str, notify=changed)
    def quantiseLabel(self) -> str:
        return QUANTISE_LABELS.get(self.e.quantise, "?")

    @Property(bool, notify=changed)
    def metronome(self) -> bool:
        return self.e.metronome

    @Property(bool, notify=changed)
    def fixedVelocity(self) -> bool:
        return self.e.fixed_velocity

    @Property(int, notify=changed)
    def layerCount(self) -> int:
        return len([layer for layer in self.e.layers if layer])

    @Property("QStringList", notify=changed)
    def kitNames(self) -> list[str]:
        return list(self._kits)

    @Property(int, notify=changed)
    def kitIndex(self) -> int:
        names = list(self._kits)
        return names.index(self._kit_name) if self._kit_name in names else 0

    @Property("QStringList", notify=changed)
    def padLabels(self) -> list[str]:
        return self.e.kit.labels() if self.e.kit else [""] * NUM_SOUNDS

    @Property(int, notify=changed)
    def selectedSound(self) -> int:
        return self.e.selected

    @Property("QStringList", notify=changed)
    def devices(self) -> list[str]:
        return self.hub.devices + self.pads.names

    @Property(str, notify=changed)
    def latencyText(self) -> str:
        return self._latency_text

    def set_latency(self, frames: int, device: str) -> None:
        self._latency_text = f"{device} · {frames / self.e.rate * 1000:.0f} ms"
        self.changed.emit()

    # --- performance ----------------------------------------------------------------------

    @Property("QVariantList", notify=changed)
    def muted(self) -> list:
        return sorted(self.e.muted)

    @Property("QVariantList", notify=changed)
    def soloed(self) -> list:
        return sorted(self.e.soloed)

    @Property("QStringList", notify=tick)
    def held(self) -> list[str]:
        return sorted(self._held)

    @Property(str, notify=tick)
    def glitchMode(self) -> str:
        return self.e.master.glitch.mode

    @Property("QVariantList", constant=True)
    def glitches(self) -> list:
        keys = {"stutter4": "5", "stutter8": "6", "stutter16": "7", "stutter32": "8", "tapestop": "9",
                "reverse": "0", "half": "-"}  # fmt: skip
        return [{"id": m, "label": label, "key": keys.get(m, "")} for m, label in GLITCHES]

    @Property("QStringList", constant=True)
    def rollRates(self) -> list[str]:
        return [label for _n, label in ROLL_RATES]

    @Property(int, notify=changed)
    def rollRateIndex(self) -> int:
        rates = [n for n, _l in ROLL_RATES]
        return rates.index(self.e.roll_rate) if self.e.roll_rate in rates else 1

    @Slot(int)
    def setRollRate(self, i: int) -> None:
        self.e.roll_rate = ROLL_RATES[max(0, min(i, len(ROLL_RATES) - 1))][0]
        self.changed.emit()

    @Slot(int, str)
    def padDown(self, sound: int, how: str = "") -> None:
        """A pad on screen or the keyboard. how: "" plays (or rolls while roll is held),
        "select" / "mute" / "solo" do that instead."""
        if self._assigning:
            return
        if how == "select":
            self.e.select(sound)
        elif how == "mute":
            self.e.toggle_mute(sound)
        elif how == "solo":
            self.e.toggle_solo(sound)
        elif not self._modify(sound):
            if "roll" in self._held:
                self.e.roll_start(sound, 1.0)
            else:
                self.e.trigger(sound, 1.0)
            self.padHit.emit(sound, 1.0)
        self.changed.emit()

    @Slot(int)
    def padUp(self, sound: int) -> None:
        self.e.roll_stop(sound)

    @Slot(str)
    def holdDown(self, name: str) -> None:
        self._hold(name, True)
        self.changed.emit()

    @Slot(str)
    def holdUp(self, name: str) -> None:
        self._hold(name, False)
        self.changed.emit()

    @Slot(int)
    def toggleMute(self, sound: int) -> None:
        self.e.toggle_mute(sound)

    @Slot(int)
    def toggleSolo(self, sound: int) -> None:
        self.e.toggle_solo(sound)

    # --- saved sounds -----------------------------------------------------------------------

    @Property("QStringList", notify=changed)
    def soundNames(self) -> list[str]:
        return files.sounds()

    @Property(str, notify=changed)
    def soundName(self) -> str:
        return self._sound_name

    @Slot(str, result=bool)
    def saveSound(self, name: str) -> bool:
        if not name.strip():
            return False
        path = files.save_sound(self.e, self._kit_name, name)
        self._sound_name = path.stem
        self.notify.emit(f"Saved sound {path.stem}")
        self.changed.emit()
        return True

    @Slot(str)
    def loadSound(self, name: str) -> None:
        try:
            d = files.read_sound(name)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't open {name}: {exc}")
            return
        names = list(self._kits)
        if d.get("kit") in names and d["kit"] != self._kit_name:
            self._load_kit(names.index(d["kit"]))
        self.e.apply_sound_settings(d.get("sound") or {})
        self._sound_name = name
        self._save_later.start()
        self.changed.emit()

    @Slot(str)
    def deleteSound(self, name: str) -> None:
        files.delete_sound(name)
        if name == self._sound_name:
            self._sound_name = ""
        self.changed.emit()

    def _step_sound(self, d: int) -> None:
        names = files.sounds()
        if not names:
            self.notify.emit("No saved sounds yet")
            return
        i = names.index(self._sound_name) if self._sound_name in names else (-1 if d > 0 else 0)
        name = names[(i + d) % len(names)]
        self.loadSound(name)
        self.notify.emit(f"Sound: {name}")

    @Property("QStringList", notify=changed)
    def gamepads(self) -> list[str]:
        return self.pads.names

    # --- step grid -----------------------------------------------------------------------

    @Property("QVariantList", notify=changed)
    def gridCells(self) -> list:
        return self.e.grid(self._page)

    @Property(int, notify=changed)
    def gridPage(self) -> int:
        return self._page

    @Property(int, notify=changed)
    def gridPages(self) -> int:
        return max(1, -(-self.e.steps() // STEPS_PER_BAR))

    @Property(bool, notify=changed)
    def followPlayhead(self) -> bool:
        return self._follow

    @Slot(int)
    def setGridPage(self, page: int) -> None:
        self._page = max(0, min(page, self.gridPages - 1))
        self._follow = False
        self.changed.emit()

    @Slot(bool)
    def setFollow(self, on: bool) -> None:
        self._follow = on
        self.changed.emit()

    @Slot(int, int)
    def toggleStep(self, sound: int, step: int) -> None:
        self.e.toggle_step(sound, self._page * STEPS_PER_BAR + step)
        self.e.select(sound)

    @Slot(int, int)
    def cycleStepVelocity(self, sound: int, step: int) -> None:
        """Soft (0.4) -> normal (0.8) -> accent (1.0) -> soft; an empty step gets an accent."""
        s = self._page * STEPS_PER_BAR + step
        v = self.e.grid(self._page)[sound][step]
        if v <= 0:
            self.e.toggle_step(sound, s, 1.0)
        else:
            self.e.set_step_velocity(sound, s, 0.8 if v < 0.6 else 1.0 if v < 0.9 else 0.4)

    # --- sound: pad and master parameters -------------------------------------------------

    def _pad_text(self, key: str, v: float, pad: dict) -> str:
        if key in ("level", "drive", "punch", "delay", "reverb"):
            return "Off" if key in ("drive", "punch") and v <= 0 else _pct(v)
        if key == "pan":
            return "C" if abs(v - 0.5) < 0.02 else f"L{(0.5 - v) * 200:.0f}" if v < 0.5 else f"R{(v - 0.5) * 200:.0f}"
        if key == "pitch":
            st = voice.semitones(pad)
            return "0" if st == 0 else f"{st:+g} st"
        if key == "attack":
            return "0" if v <= 0 else _secs(0.5 * v * v)
        if key == "sustain":
            return "Full" if v >= 1 else _secs(1.5 * v * v)
        if key == "decay":
            return "Full" if v >= 1 else _secs((0.008 + 0.8 * v * v) * 6.9)  # to -60 dB
        if key == "cutoff":
            return "Open" if not voice.filter_on(pad) else _hz(dsp.cutoff_hz(v))
        if key == "resonance":
            return f"Q {dsp.resonance_q(v):.1f}"
        return _pct(v)

    def _master_text(self, key: str, v: float) -> str:
        s = self.e.master_settings
        if key == "filter":
            setting = dsp.dj_setting(v, s["resonance"])
            return "Off" if setting is None else ("LP " if setting[0] == "lp" else "HP ") + _hz(setting[1])
        if key in ("delay", "reverb", "shimmer", "drive", "comp"):
            return "Off" if v <= 0 else _pct(v)
        if key == "size":
            return _secs(self.e.master.reverb.rt60())
        if key == "tone":
            return _hz(1200.0 * (14000.0 / 1200.0) ** v)
        if key == "limit":
            return "Safety" if v <= 0 else f"+{v * 18:.0f} dB"
        if key == "swing":
            return "Off" if v <= 0 else f"{50 * (1 + v * MAX_SWING):.0f}%"
        return _pct(v)

    @Property("QVariantList", notify=changed)
    def padParams(self) -> list:
        pad = self.e.pads[self.e.selected]
        got = self.s.params_assigned()
        page = KNOB_PAGES[self.e.knob_page]
        out = []
        for key, (label, default) in voice.PAD_KNOBS.items():
            pid = f"p.{key}"
            knob = page.index(pid) + 1 if pid in page and page.index(pid) < self.knobCount else 0
            out.append({
                "id": pid, "label": label, "value": pad[key], "default": default,
                "text": self._pad_text(key, pad[key], pad), "assigned": ", ".join(got.get(pid, [])), "knob": knob,
            })  # fmt: skip
        return out

    @Property("QVariantList", notify=changed)
    def masterParams(self) -> list:
        s = self.e.master_settings
        got = self.s.params_assigned()
        page = KNOB_PAGES[self.e.knob_page]
        out = []
        for key, (label, default) in MASTER_KNOBS.items():
            pid = f"m.{key}"
            knob = page.index(pid) + 1 if pid in page and page.index(pid) < self.knobCount else 0
            out.append({
                "id": pid, "label": label, "value": s[key], "default": default,
                "text": self._master_text(key, s[key]), "assigned": ", ".join(got.get(pid, [])), "knob": knob,
            })  # fmt: skip
        return out

    def _choices(self, scope: str, spec: dict, values: dict) -> list:
        out = []
        for key, (label, _default, options) in spec.items():
            if options == (False, True):
                continue  # switches, not lists
            names = CHOICE_LABELS.get(key, {})
            out.append({
                "id": f"{scope}.{key}", "label": label, "options": [str(names.get(o, o)) for o in options],
                "index": list(options).index(values[key]) if values[key] in options else 0,
            })  # fmt: skip
        return out

    @Property("QVariantList", notify=changed)
    def padChoices(self) -> list:
        return self._choices("p", voice.PAD_CHOICES, self.e.pads[self.e.selected])

    @Property("QVariantList", notify=changed)
    def masterChoices(self) -> list:
        return self._choices("m", MASTER_CHOICES, self.e.master_settings)

    @Property(bool, notify=changed)
    def padReverse(self) -> bool:
        return bool(self.e.pads[self.e.selected]["reverse"])

    @Property(bool, notify=changed)
    def pingPong(self) -> bool:
        return bool(self.e.master_settings["pingpong"])

    @Property("QStringList", constant=True)
    def presets(self) -> list[str]:
        return list(PRESETS)

    @Property(str, notify=changed)
    def knobPage(self) -> str:
        return self.e.knob_page

    @Property(float, notify=tick)
    def compReduction(self) -> float:
        return float(self.e.master.comp.reduction_db + self.e.master.limiter.reduction_db)

    @Slot(str, float)
    def setParam(self, pid: str, v: float) -> None:
        self.e.set_param(pid, v)

    @Slot(str)
    def resetParam(self, pid: str) -> None:
        scope, key = pid.split(".", 1)
        default = voice.PAD_KNOBS[key][1] if scope == "p" else MASTER_KNOBS[key][1]
        if pid == "p.cutoff":
            default = voice.OPEN_CUTOFF[self.e.pads[self.e.selected]["filter_type"]]
        self.e.set_param(pid, default)

    @Slot(str, int)
    def setChoice(self, pid: str, index: int) -> None:
        scope, key = pid.split(".", 1)
        spec = voice.PAD_CHOICES if scope == "p" else MASTER_CHOICES
        options = spec[key][2]
        self.e.set_param(pid, options[max(0, min(index, len(options) - 1))])

    @Slot(str, bool)
    def setSwitch(self, pid: str, on: bool) -> None:
        self.e.set_param(pid, bool(on))

    @Slot(str)
    def applyPreset(self, name: str) -> None:
        self.e.apply_preset(name)
        self.notify.emit(f"Master: {name}")

    @Slot()
    def resetPad(self) -> None:
        i = self.e.selected
        for key, value in voice.default_pad().items():
            self.e.set_pad(i, key, value)

    @Slot()
    def resetMaster(self) -> None:
        keep = {k: self.e.master_settings[k] for k in ("swing", "volume")}
        for key, value in {**master_defaults(), **keep}.items():
            self.e.set_master(key, value)

    @Slot(str)
    def setKnobPage(self, page: str) -> None:
        if page in KNOB_PAGES:
            self.e.knob_page = page
            self.e._changed()
            self._save_settings()

    # --- controller assignments, loops -----------------------------------------------------

    @Property(str, notify=changed)
    def assigning(self) -> str:
        return self._assigning

    @Property(str, notify=changed)
    def assigningLabel(self) -> str:
        a = self._assigning
        if a.startswith("sound:"):
            return self.padLabels[int(a[6:])]
        if a.startswith("action:"):
            return ACTIONS.get(a[7:], a)
        if a.startswith("param:"):
            return self._label(a[6:])
        return ""

    @Property("QVariantList", notify=changed)
    def actionList(self) -> list:
        got = self.s.actions_assigned()
        return [{"id": a, "label": label, "assigned": ", ".join(got.get(a, []))} for a, label in ACTIONS.items()]

    @Property("QStringList", notify=changed)
    def savedLoops(self) -> list[str]:
        return files.saved()

    @Property(str, constant=True)
    def exportDir(self) -> str:
        return str(files.EXPORT_DIR)

    # --- slots -----------------------------------------------------------------------------

    @Slot(int)
    def hitPad(self, sound: int) -> None:
        if self._assigning:
            return
        self.e.trigger(sound, 1.0)
        self.padHit.emit(sound, 1.0)

    @Slot(int)
    def selectPad(self, sound: int) -> None:
        self.e.select(sound)

    @Slot(str)
    def runAction(self, action: str) -> None:
        e = self.e
        if action == "record":
            e.record()
        elif action == "play":
            e.play_stop()
            if e.state == "playing":
                self._follow = True
        elif action == "undo":
            e.undo()
        elif action == "clear":
            e.clear()
            self._page = 0
        elif action == "quantise":
            i = QUANTISE_STEPS.index(e.quantise) if e.quantise in QUANTISE_STEPS else 0
            self.setQuantise(QUANTISE_STEPS[(i + 1) % len(QUANTISE_STEPS)])
            self.notify.emit(f"Quantise {self.quantiseLabel}")
        elif action == "metronome":
            self.setMetronome(not e.metronome)
        elif action == "kit":
            self.setKit(self.kitIndex + 1)
            self.notify.emit(f"Kit: {self._kit_name}")
        elif action == "page":
            pages = list(KNOB_PAGES)
            self.setKnobPage(pages[(pages.index(e.knob_page) + 1) % len(pages)])
            self.notify.emit(f"Knobs: {KNOB_PAGE_LABELS[e.knob_page]}")
        elif action == "sound_next":
            self._step_sound(1)
        elif action == "sound_prev":
            self._step_sound(-1)
        self.changed.emit()

    @Slot(float)
    def setBpm(self, bpm: float) -> None:
        with self.e.lock:
            if self.e.state == "empty":
                self.e.bpm = max(40.0, min(300.0, bpm))
            else:
                # A recorded loop keeps its length; its tempo is what it is.
                self.notify.emit("The tempo of a recorded loop comes from its length: clear it to change")
        self._save_settings()
        self.changed.emit()

    @Slot()
    def tapTempo(self) -> None:
        now = time.monotonic()
        self._taps = [t for t in self._taps if now - t < 2.5] + [now]
        if len(self._taps) >= 3:
            gaps = [b - a for a, b in zip(self._taps, self._taps[1:], strict=False)]
            self.setBpm(60.0 / (sum(gaps) / len(gaps)))

    @Slot(int)
    def setBars(self, bars: int) -> None:
        with self.e.lock:
            if self.e.state == "empty":
                self.e.bars = max(0, bars)
        self._save_settings()
        self.changed.emit()

    @Slot(int)
    def setQuantise(self, q: int) -> None:
        self.e.quantise = q if q in QUANTISE_STEPS else 0
        self._save_settings()
        self.changed.emit()

    @Slot(bool)
    def setMetronome(self, on: bool) -> None:
        self.e.metronome = on
        self._save_settings()
        self.changed.emit()

    @Slot(bool)
    def setFixedVelocity(self, on: bool) -> None:
        self.e.fixed_velocity = on
        self._save_settings()
        self.changed.emit()

    @Slot(int)
    def setKit(self, index: int) -> None:
        self._load_kit(index)
        self._save_settings()
        self.changed.emit()

    @Slot(str)
    def assign(self, target: str) -> None:
        """target 'sound:N', 'action:name' or 'param:p.x': the next control used gets it. '' cancels."""
        self._assigning = target
        self.changed.emit()

    @Slot(str)
    def unassign(self, target: str) -> None:
        self.s.clear_assign(target)
        self._save_settings()
        self.changed.emit()

    @Slot(str, result=bool)
    def saveLoop(self, name: str) -> bool:
        if not self.e.length:
            self.notify.emit("Nothing to save yet")
            return False
        path = files.save(self.e, self._kit_name, name)
        self.notify.emit(f"Saved {path.stem}")
        self.changed.emit()
        return True

    @Slot(str)
    def loadLoop(self, name: str) -> None:
        try:
            d = files.read(name)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Couldn't open {name}: {exc}")
            return
        names = list(self._kits)
        if d.get("kit") in names:
            self._load_kit(names.index(d["kit"]))
        files.from_dict(self.e, d)
        self._page = 0
        self.notify.emit(f"Loaded {name} — press Play")
        self.changed.emit()

    @Slot(str)
    def deleteLoop(self, name: str) -> None:
        files.delete(name)
        self.changed.emit()

    @Slot(str, int, result=str)
    def exportWav(self, name: str, repeats: int) -> str:
        return self._export(name, ".wav", lambda p: files.export_wav(self.e, p, repeats))

    @Slot(str, result=str)
    def exportMidi(self, name: str) -> str:
        return self._export(name, ".mid", lambda p: files.export_midi(self.e, p))

    def _export(self, name: str, ext: str, fn) -> str:
        if not self.e.length:
            self.notify.emit("Nothing to export yet")
            return ""
        path = Path(files.EXPORT_DIR) / f"{files.safe_name(name)}{ext}"
        try:
            fn(path)
        except (OSError, ValueError) as exc:
            self.notify.emit(f"Export failed: {exc}")
            return ""
        self.notify.emit(f"Exported {path}")
        return str(path)
