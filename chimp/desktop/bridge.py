"""QML-facing controller for desktop actions: LPD8 tiles and generic MIDI devices."""

from __future__ import annotations

import configparser
import json
import os
import threading
import time
from collections import Counter
from pathlib import Path

from PySide6.QtCore import Property, QFileSystemWatcher, QObject, QTimer, Signal, Slot

from .. import controllers
from . import system, templates, unit
from .config import (
    BUTTON_ACTIONS,
    CONFIG_PATH,
    KNOB_ACTIONS,
    MEDIA_SHORTCUTS,
    ON_COLOURS,
    OVERLAY_POSITIONS,
    PAD_ACTIONS,
    Action,
    DesktopConfig,
    DeviceConfig,
    Profile,
)
from .learn import _address, classify, decode
from .midi import MidiHub
from .service import STATUS_PATH

DIM = (20, 20, 70)
LPD8 = "LPD8 mk2"


def _types(table: dict) -> list[dict]:
    # "file": the target is a file, picked with Browse...; "sound": the soundboard's settings
    return [
        {"id": k, "label": v[0], "targetLabel": v[1], "hint": v[2], "file": k == "sound", "sound": k == "sound"}
        for k, v in table.items()
    ]


def _option_value(key: str, value):
    """A setting from the editor, as stored: volume is a whole percentage."""
    if key == "volume":
        try:
            return max(0, min(100, round(float(value))))
        except (TypeError, ValueError):
            return 100
    return str(value)[:200]


def _installed_apps() -> list[dict]:
    dirs = [os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))]
    dirs += os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    dirs += [str(Path.home() / ".local/share/flatpak/exports/share"), "/var/lib/flatpak/exports/share"]
    apps: dict[str, str] = {}
    for d in dirs:
        folder = Path(d) / "applications"
        if not folder.is_dir():
            continue
        for f in folder.glob("*.desktop"):
            if f.stem in apps:
                continue
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
                continue
            if e.get("NoDisplay", "false").lower() == "true" or e.get("Type", "Application") != "Application":
                continue
            apps[f.stem] = e.get("Name", f.stem)
    return sorted(({"value": k, "label": f"{v}  ({k})"} for k, v in apps.items()), key=lambda r: r["label"].lower())


def _live_for(a: Action, cache: dict, targets: dict | None = None) -> dict:
    """Current desktop state behind one action (runs on a worker thread)."""

    def vol(node):
        if node not in cache:
            cache[node] = system.get_volume(node)
        return cache[node]

    v, muted, text, on = None, False, "", False
    if a.type == "master_volume":
        v, muted = vol(system.SINK)
    elif a.type == "mic_volume":
        v, muted = vol(system.SOURCE)
    elif a.type in system.STREAM_ACTIONS:
        streams, label = system.resolve_streams(a.type, a.target, targets or {})
        if streams:
            v, muted = vol(streams[0]["id"])
            if a.type != "app_volume":
                text = label
        else:
            text = label if a.type != "app_volume" else "not playing"
    elif a.type == "brightness":
        v = system.get_brightness(a.target)
    elif a.type == "mic_mute":
        on = vol(system.SOURCE)[1]
        text = "Muted" if on else "Live"
    elif a.type == "output_mute":
        on = vol(system.SINK)[1]
        text = "Muted" if on else "On"
    if v is not None:
        text = (f"{text} · " if text else "") + f"{round(v * 100)}%" + (" · muted" if muted else "")
    return {"value": -1 if v is None else min(v, 1.0), "text": text, "muted": muted, "on": on}


def _read_live(items: list[tuple[str, Action]], targets: dict | None = None) -> dict:
    cache: dict = {}
    return {key: _live_for(a, cache, targets) for key, a in items if a.type != "none"}


class DesktopBridge(QObject):
    revisionChanged = Signal()
    serviceChanged = Signal()
    liveChanged = Signal()
    lastActionChanged = Signal()
    devicesChanged = Signal()
    deviceChanged = Signal()  # current device / profile / its controls
    learnChanged = Signal()
    controlMoved = Signal(int, float, str)  # control index, 0-1 level (-1 = n/a), text
    notify = Signal(str, str)
    _liveReady = Signal(object)

    def __init__(self, backend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self.cfg = DesktopConfig.load()
        self._revision = 0
        self._preview = None  # a Soundboard for trying clips, made on first use
        self._running = False
        self._paused = False
        self._live: dict = {}
        self._live_rev = 0
        self._live_busy = False
        self._last_action = ""
        self._last_time = 0.0
        self._status_devices: dict = {}
        self._targets: dict = {}
        self._cache: dict[str, list[dict]] = {}

        self._device = LPD8
        self._profile = 0
        self._group = ""
        self._learning = False
        self._learn_events: list[tuple[float, list[int]]] = []
        self._learn_preview: list = []

        self.hub = MidiHub("Chimp", exclude={LPD8}, parent=self)
        self.hub.devicesChanged.connect(self.devicesChanged)
        self.hub.message.connect(self._on_midi)
        self.hub.start()
        backend.connectionChanged.connect(self.devicesChanged)

        # The tray can change settings too: reload when the file changes under us.
        self._last_saved = CONFIG_PATH.read_text() if CONFIG_PATH.exists() else ""
        self._watcher = QFileSystemWatcher([str(CONFIG_PATH.parent)], self)
        if CONFIG_PATH.exists():
            self._watcher.addPath(str(CONFIG_PATH))
        self._reload_timer = QTimer(self, singleShot=True, interval=250, timeout=self._external_change)
        self._watcher.fileChanged.connect(lambda _: self._reload_timer.start())
        self._watcher.directoryChanged.connect(lambda _: self._reload_timer.start())

        self._liveReady.connect(self._on_live)
        self._save_timer = QTimer(self, singleShot=True, interval=300, timeout=self._save)
        self._live_timer = QTimer(self, interval=500, timeout=self._tick)
        self._live_timer.start()
        self._learn_timer = QTimer(self, interval=200, timeout=self._update_learn)
        self._service_timer = QTimer(self, interval=3000, timeout=self._refresh_service)
        self._service_timer.start()
        backend.currentSlotChanged.connect(self._tick)
        self._refresh_service()
        self._tick()

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.hub.stop()

    # --- common properties -----------------------------------------------------------

    @Property(int, notify=revisionChanged)
    def revision(self) -> int:
        return self._revision

    @Property(bool, notify=serviceChanged)
    def serviceRunning(self) -> bool:
        return self._running

    @Property(bool, notify=serviceChanged)
    def paused(self) -> bool:
        return self._paused and self._running

    @Property(bool, notify=revisionChanged)
    def osd(self) -> bool:
        return self.cfg.osd

    @Property(bool, notify=revisionChanged)
    def leds(self) -> bool:
        return self.cfg.leds

    @Property(int, notify=liveChanged)
    def liveRevision(self) -> int:
        return self._live_rev

    @Property(str, notify=lastActionChanged)
    def lastAction(self) -> str:
        return self._last_action

    @Property(float, notify=lastActionChanged)
    def lastActionTime(self) -> float:
        return self._last_time

    @Slot(result="QVariantList")
    def knobTypes(self) -> list:
        return _types(KNOB_ACTIONS)

    @Slot(result="QVariantList")
    def padTypes(self) -> list:
        return _types(PAD_ACTIONS)

    @Slot(result="QVariantList")
    def buttonTypes(self) -> list:
        return _types(BUTTON_ACTIONS)

    @Slot(str, result="QVariantList")
    def suggestions(self, kind: str) -> list:
        """Picker entries for an action's target: [{value, label}]."""
        if kind == "app_volume":
            names = set(system.app_names())
            names |= {a.target for pa in self.cfg.programs.values() for a in pa.knobs if a.type == "app_volume"}
            names |= {
                c.action.target
                for d in self.cfg.devices.values()
                for p in d.profiles
                for c in p.controls
                if c.action.type == "app_volume"
            }
            return [{"value": n, "label": n} for n in sorted((n for n in names if n), key=str.lower)]
        if kind == "brightness":
            return [{"value": "all", "label": "All displays"}] + [
                {"value": d["label"], "label": d["label"]} for d in system.displays()
            ]
        if kind == "media":
            return [{"value": k, "label": k} for k in MEDIA_SHORTCUTS]
        if kind == "sound":
            from .soundboard import clip_label

            return [{"value": t, "label": clip_label(t) + "  ·  " + t} for t in sorted(set(self.cfg.sound_targets()))]
        if kind == "playing_volume":
            playing = [label for label, _ in system.playing_groups()]
            return [
                {"value": str(n), "label": f"{n}" + (f"  (now: {playing[n - 1]})" if n <= len(playing) else "")}
                for n in range(1, 9)
            ]
        if kind == "shortcut":
            if "shortcut" not in self._cache:
                self._cache["shortcut"] = system.list_shortcuts()
            return self._cache["shortcut"]
        if kind == "launch":
            if "launch" not in self._cache:
                self._cache["launch"] = _installed_apps()
            return self._cache["launch"]
        return []

    def _changed(self, bump: bool = True, device: bool = False) -> None:
        if bump:
            self._revision += 1
            self.revisionChanged.emit()
        if device:
            self.deviceChanged.emit()
        self._save_timer.start()

    def _save(self) -> None:
        try:
            self.cfg.save()
            self._last_saved = CONFIG_PATH.read_text()
        except OSError as e:
            self.notify.emit(f"Couldn't save desktop settings: {e}", "error")

    def _external_change(self) -> None:
        if str(CONFIG_PATH) not in self._watcher.files() and CONFIG_PATH.exists():
            self._watcher.addPath(str(CONFIG_PATH))
        if self._save_timer.isActive():
            return  # our own edit is about to be written
        try:
            text = CONFIG_PATH.read_text()
        except OSError:
            return
        if text == self._last_saved:
            return
        self._last_saved = text
        self.cfg = DesktopConfig.load()
        self._changed(bump=True, device=True)
        self._save_timer.stop()
        self.devicesChanged.emit()

    # --- overlay settings ----------------------------------------------------------------

    @Property(bool, notify=revisionChanged)
    def overlay(self) -> bool:
        return self.cfg.overlay

    @Property(str, notify=revisionChanged)
    def overlayPosition(self) -> str:
        return self.cfg.overlay_position

    @Property(bool, notify=revisionChanged)
    def overlayOverGames(self) -> bool:
        return self.cfg.overlay_over_games

    @Slot(bool)
    def setOverlay(self, on: bool) -> None:
        self.cfg.overlay = on
        self._changed()

    @Slot(str)
    def setOverlayPosition(self, pos: str) -> None:
        if pos in OVERLAY_POSITIONS:
            self.cfg.overlay_position = pos
            self._changed()

    @Slot(bool)
    def setOverlayOverGames(self, on: bool) -> None:
        self.cfg.overlay_over_games = on
        self._changed()

    @Slot(bool)
    def setOsd(self, on: bool) -> None:
        self.cfg.osd = on
        self._changed()

    @Slot(bool)
    def setLeds(self, on: bool) -> None:
        self.cfg.leds = on
        self._changed()

    # --- LPD8 per-program actions ------------------------------------------------------

    @Slot(int, bool, int, result="QVariantMap")
    def action(self, slot: int, pad: bool, i: int) -> dict:
        pa = self.cfg.actions(slot)
        a = (pa.pads if pad else pa.knobs)[i]
        return {"type": a.type, "target": a.target, "options": dict(a.options)}

    @Slot(int, result=bool)
    def hasActions(self, slot: int) -> bool:
        return self.cfg.actions(slot).active()

    @Slot(int, result=str)
    def programJob(self, slot: int) -> str:
        """What an LPD8 program is for: "Desktop", or its job in Chimp Loop ("Loop A")."""
        from ..loop import programs

        if self.cfg.actions(slot).active():
            return "Desktop"
        job = programs.lpd8_jobs(self.cfg.slots_with_actions(), programs.saved_jobs()).get(slot, "")
        return programs.describe(job) if job else ""

    @Slot(bool, int, result="QVariantMap")
    def live(self, pad: bool, i: int) -> dict:
        return self._live.get(("p" if pad else "k") + str(i), {"value": -1, "text": "", "on": False})

    @Slot(int, bool, int, str)
    def setType(self, slot: int, pad: bool, i: int, kind: str) -> None:
        table = PAD_ACTIONS if pad else KNOB_ACTIONS
        if kind not in table:
            return
        pa = self.cfg.actions(slot)
        items = pa.pads if pad else pa.knobs
        if items[i].type == kind:
            return
        items[i] = self._new_action(kind, table)
        self._changed()
        self._tick()

    @Slot(int, bool, int, str)
    def setTarget(self, slot: int, pad: bool, i: int, target: str) -> None:
        pa = self.cfg.actions(slot)
        items = pa.pads if pad else pa.knobs
        if items[i].target != target:
            items[i].target = target
            self._changed(bump=False)  # don't rebuild the field the user is typing in

    @Slot(int, bool, int, str, "QVariant")
    def setOption(self, slot: int, pad: bool, i: int, key: str, value) -> None:
        pa = self.cfg.actions(slot)
        a = (pa.pads if pad else pa.knobs)[i]
        v = _option_value(key, value)
        if a.options.get(key) != v:
            a.options[key] = v
            self._changed(bump=key != "volume")  # (a slider being dragged needn't rebuild the tile)

    # --- soundboard: the editor plays clips itself, to try them ----------------------------

    @Slot(result="QVariantList")
    def soundModes(self) -> list:
        from .soundboard import MODES

        return [{"id": k, "label": v} for k, v in MODES.items()]

    @Slot(result="QVariantList")
    def soundOutputs(self) -> list:
        """[{id, label}]: "" is the default output, the rest by name."""
        from .soundboard import output_devices

        return [{"id": "", "label": "Default output"}] + [{"id": d, "label": d} for d in output_devices()]

    @Slot(str, "QVariantMap")
    def previewSound(self, target: str, options: dict) -> None:
        if self._preview is None:
            from .soundboard import Soundboard

            self._preview = Soundboard(lambda text: self.notify.emit(text, "warning"), parent=self)
        opts = dict(options or {})
        opts["mode"] = "restart"
        self._preview.press("preview", target, opts)

    @Slot()
    def stopPreview(self) -> None:
        if self._preview is not None:
            self._preview.stop_all()

    @Slot(result=str)
    def soundFolder(self) -> str:
        """Where Browse... starts: the folder of the last clip chosen, or Music."""
        from .soundboard import clip_path

        for t in reversed(self.cfg.sound_targets()):
            folder = clip_path(t).parent
            if folder.is_dir():
                return str(folder)
        music = Path.home() / "Music"
        return str(music if music.is_dir() else Path.home())

    @staticmethod
    def _new_action(kind: str, table: dict) -> Action:
        default = {"media": "playpause", "brightness": "all", "playing_volume": "1"}.get(kind, "")
        return Action(kind, default if table[kind][1] else "")

    @Slot(int)
    def clearActions(self, slot: int) -> None:
        pa = self.cfg.actions(slot)
        pa.knobs = [Action() for _ in pa.knobs]
        pa.pads = [Action() for _ in pa.pads]
        self._changed()

    @Slot(int)
    def matchPadColours(self, slot: int) -> None:
        """Dim pads, with a highlight colour per action (editor working copy; Send to apply)."""
        for i, a in enumerate(self.cfg.actions(slot).pads):
            if a.type == "none":
                continue
            self._backend.setPad(i, "off", "#{:02x}{:02x}{:02x}".format(*DIM))
            self._backend.setPad(i, "on", "#{:02x}{:02x}{:02x}".format(*ON_COLOURS.get(a.type, (255, 255, 255))))

    # --- devices ------------------------------------------------------------------------

    @Property("QVariantList", notify=devicesChanged)
    def devices(self) -> list:
        """[{name, connected, lpd8}] — the LPD8 first, then every other controller seen or set up."""
        out = [{"name": LPD8, "connected": bool(self._backend.connected), "lpd8": True}]
        connected = set(self.hub.devices)
        for name in sorted(connected | set(self.cfg.devices), key=str.lower):
            out.append({"name": name, "connected": name in connected, "lpd8": False})
        return out

    @Property(str, notify=deviceChanged)
    def currentDevice(self) -> str:
        return self._device

    @Property(bool, notify=deviceChanged)
    def isLpd8(self) -> bool:
        return self._device == LPD8

    @Slot(str)
    def setCurrentDevice(self, name: str) -> None:
        if name == self._device:
            return
        if self._learning:
            self.cancelLearn()
        self._device = name
        self._profile = self._service_profile(name)
        self._group = ""
        self._auto_template()
        self.deviceChanged.emit()
        self._tick()

    def _dev(self) -> DeviceConfig | None:
        return self.cfg.devices.get(self._device)

    def _ensure_dev(self) -> DeviceConfig:
        dev = self.cfg.devices.get(self._device)
        if dev is None:
            dev = self.cfg.devices[self._device] = DeviceConfig()
            self.devicesChanged.emit()
        return dev

    def _prof(self) -> Profile | None:
        dev = self._dev()
        if dev is None:
            return None
        self._profile = min(self._profile, len(dev.profiles) - 1)
        return dev.profiles[self._profile]

    @Property(bool, notify=deviceChanged)
    def deviceConnected(self) -> bool:
        return self._device in self.hub.devices

    @Property(bool, notify=deviceChanged)
    def deviceEnabled(self) -> bool:
        dev = self._dev()
        return dev.enabled if dev else True

    @Slot(bool)
    def setDeviceEnabled(self, on: bool) -> None:
        self._ensure_dev().enabled = on
        self._changed(device=True)

    @Slot()
    def forgetDevice(self) -> None:
        if self.cfg.devices.pop(self._device, None) is not None:
            self._profile = 0
            self._changed(device=True)
            self.devicesChanged.emit()

    # --- profiles -----------------------------------------------------------------------

    @Property("QVariantList", notify=deviceChanged)
    def profileNames(self) -> list:
        dev = self._dev()
        return [p.name for p in dev.profiles] if dev else ["Default"]

    @Property(int, notify=deviceChanged)
    def currentProfile(self) -> int:
        return self._profile

    @Property(int, notify=deviceChanged)
    def activeProfile(self) -> int:
        """Which profile the background service is using right now (-1 if unknown)."""
        info = self._status_devices.get(self._device)
        return int(info.get("profile", 0)) if info else -1

    def _service_profile(self, name: str) -> int:
        info = self._status_devices.get(name)
        return int(info.get("profile", 0)) if info else 0

    @Slot(int)
    def setCurrentProfile(self, i: int) -> None:
        dev = self._dev()
        if dev and 0 <= i < len(dev.profiles) and i != self._profile:
            self._profile = i
            self.deviceChanged.emit()
            self._tick()

    @Slot(str, bool)
    def addProfile(self, name: str, copy_current: bool) -> None:
        dev = self._ensure_dev()
        new = Profile(name.strip() or f"Profile {len(dev.profiles) + 1}")
        cur = self._prof()
        if copy_current and cur:
            new.controls = [type(c).from_dict(c.to_dict()) for c in cur.controls]
        elif cur:  # same physical controls, no actions
            new.controls = [type(c).from_dict({**c.to_dict(), "action": {}}) for c in cur.controls]
        dev.profiles.append(new)
        self._profile = len(dev.profiles) - 1
        self._changed(device=True)

    @Slot(str)
    def renameProfile(self, name: str) -> None:
        prof = self._prof()
        if prof and name.strip():
            prof.name = name.strip()
            self._changed(device=True)

    @Slot()
    def deleteProfile(self) -> None:
        dev = self._dev()
        if not dev or len(dev.profiles) <= 1:
            return
        dev.profiles.pop(self._profile)
        self._profile = max(0, self._profile - 1)
        self._changed(device=True)

    # --- controls -----------------------------------------------------------------------

    @Property(int, notify=deviceChanged)
    def controlCount(self) -> int:
        prof = self._prof()
        return len(prof.controls) if prof else 0

    @Slot(int, result="QVariantMap")
    def control(self, i: int) -> dict:
        prof = self._prof()
        if not prof or not 0 <= i < len(prof.controls):
            return {}
        c = prof.controls[i]
        return {
            "name": c.name,
            "kind": c.kind,
            "mode": c.mode,
            "where": c.describe(),
            "group": c.group,
            "action": {"type": c.action.type, "target": c.action.target, "options": dict(c.action.options)},
        }

    @Slot(int, result="QVariantMap")
    def controlLive(self, i: int) -> dict:
        return self._live.get(f"c{i}", {"value": -1, "text": "", "on": False})

    def _control(self, i: int):
        prof = self._prof()
        return prof.controls[i] if prof and 0 <= i < len(prof.controls) else None

    @Slot(int, str)
    def setControlName(self, i: int, name: str) -> None:
        c = self._control(i)
        if c and name.strip() and c.name != name.strip():
            c.name = name.strip()
            self._changed(bump=False)

    @Slot(int, str)
    def setControlKind(self, i: int, kind_mode: str) -> None:
        """'knob', 'fader', 'button:momentary', 'button:toggle', 'encoder:rel64', 'encoder:rel2c'."""
        c = self._control(i)
        if not c:
            return
        kind, _, mode = kind_mode.partition(":")
        mode = mode or "absolute"
        was_button = c.kind == "button"
        c.kind, c.mode = kind, mode
        if was_button != (kind == "button"):
            c.action = Action()  # button and knob actions don't mix
        self._changed(device=True)
        self._tick()

    @Slot(int, str)
    def setControlType(self, i: int, kind: str) -> None:
        c = self._control(i)
        table = BUTTON_ACTIONS if c and c.kind == "button" else KNOB_ACTIONS
        if not c or kind not in table or c.action.type == kind:
            return
        c.action = self._new_action(kind, table)
        self._changed(device=True)
        self._tick()

    @Slot(int, str)
    def setControlTarget(self, i: int, target: str) -> None:
        c = self._control(i)
        if c and c.action.target != target:
            c.action.target = target
            self._changed(bump=False)

    @Slot(int, str, "QVariant")
    def setControlOption(self, i: int, key: str, value) -> None:
        c = self._control(i)
        v = _option_value(key, value)
        if c and c.action.options.get(key) != v:
            c.action.options[key] = v
            self._changed(device=True)

    @Slot(int)
    def removeControl(self, i: int) -> None:
        prof = self._prof()
        if prof and 0 <= i < len(prof.controls):
            prof.controls.pop(i)
            self._changed(device=True)
            self._tick()

    # --- learning -----------------------------------------------------------------------

    @Property(bool, notify=learnChanged)
    def learning(self) -> bool:
        return self._learning

    @Property("QVariantList", notify=learnChanged)
    def learnPreview(self) -> list:
        prof = self._prof()
        known = {c.address for c in prof.controls} if prof else set()
        return [
            {"name": c.name, "kind": c.kind, "mode": c.mode, "where": c.describe(), "known": c.address in known}
            for c in self._learn_preview
        ]

    @Slot()
    def startLearn(self) -> None:
        self._learning = True
        self._learn_events = []
        self._learn_preview = []
        self._learn_timer.start()
        self.learnChanged.emit()

    @Slot()
    def cancelLearn(self) -> None:
        self._learning = False
        self._learn_timer.stop()
        self._learn_events = []
        self._learn_preview = []
        self.learnChanged.emit()

    @Slot()
    def finishLearn(self) -> None:
        self._update_learn()
        learned = self._learn_preview
        self.cancelLearn()
        if not learned:
            self.notify.emit("Nothing was detected. Move some knobs or press some buttons while learning.", "warning")
            return
        dev = self._ensure_dev()
        prof = self._prof() or dev.profiles[0]
        known = {c.address for c in prof.controls}
        counts = Counter(c.kind for c in prof.controls)
        added = 0
        for c in learned:
            if c.address in known:
                continue
            counts[c.kind] += 1
            c.name = f"{c.kind.capitalize()} {counts[c.kind]}"
            prof.controls.append(c)
            added += 1
        known = templates.layout_for(self._device)
        if known and added:
            templates.apply(known, prof.controls)  # known device: name and group the new ones straight away
            self._group = ""
        self._changed(device=True)
        self.devicesChanged.emit()
        self._tick()
        self.notify.emit(
            f"Added {added} control{'s' if added != 1 else ''} to {self._device} · {prof.name}"
            if added
            else "Those controls were already learned",
            "positive" if added else "info",
        )

    # --- groups / banks ---------------------------------------------------------------

    @Property("QVariantList", notify=deviceChanged)
    def groupNames(self) -> list:
        prof = self._prof()
        return templates.group_names(prof.controls) if prof else []

    @Property(str, notify=deviceChanged)
    def currentGroup(self) -> str:
        names = self.groupNames
        if names and self._group not in names:
            self._group = names[0]
        return self._group if names else ""

    @Slot(str)
    def setCurrentGroup(self, name: str) -> None:
        if name != self._group:
            self._group = name
            self.deviceChanged.emit()

    @Property(str, notify=deviceChanged)
    def templateLabel(self) -> str:
        """The controller Chimp knows this device as ("" if it doesn't)."""
        known = templates.layout_for(self._device)
        return known.name if known else ""

    @Property(str, notify=deviceChanged)
    def templateAbout(self) -> str:
        known = templates.layout_for(self._device)
        return known.about if known else ""

    @Property(int, notify=deviceChanged)
    def templateMissing(self) -> int:
        """How many of the known controller's controls this profile doesn't have yet."""
        known = templates.layout_for(self._device)
        if not known:
            return 0
        prof = self._prof()
        have = {c.address for c in prof.controls} if prof else set()
        return sum(1 for c in templates.controls_of(known) if c.address not in have)

    @Property(str, notify=deviceChanged)
    def deviceShape(self) -> str:
        """For a controller Chimp only knows the shape of: what to say about it."""
        c = controllers.find(self._device)
        return c.about if c and not templates.layout_for(self._device) else ""

    def _auto_template(self) -> None:
        """Known devices (the KO II) get their layout as soon as they have ungrouped controls."""
        known = templates.layout_for(self._device)
        prof = self._prof()
        if known and prof and prof.controls and not any(c.group for c in prof.controls):
            _added, arranged = templates.apply(known, prof.controls)
            if arranged:
                self._group = ""
                self._changed(device=False)

    @Slot()
    def applyTemplate(self) -> None:
        """Set the device up as the controller Chimp knows: add what's missing, name and
        group the rest. Actions already given to controls stay."""
        known = templates.layout_for(self._device)
        if not known:
            return
        self._ensure_dev()
        prof = self._prof()
        added, arranged = templates.apply(known, prof.controls, add_missing=True)
        self._group = ""
        self._changed(device=True)
        self.devicesChanged.emit()
        self._tick()
        if added:
            self.notify.emit(f"Set up as {known.name}: {added} control{'s' if added != 1 else ''} added", "positive")
        else:
            self.notify.emit(f"Arranged as {known.name}" if arranged else f"Already set up as {known.name}", "info")

    @Slot(int)
    def splitBanks(self, size: int) -> None:
        prof = self._prof()
        if prof and size > 0:
            n = templates.split_banks(prof.controls, size)
            self._group = ""
            self._changed(device=True)
            self._tick()
            self.notify.emit(f"Split the buttons into {n} bank{'s' if n != 1 else ''} of {size}", "positive")

    @Slot()
    def clearGroups(self) -> None:
        prof = self._prof()
        if prof:
            templates.clear_groups(prof.controls)
            self._group = ""
            self._changed(device=True)

    def _update_learn(self) -> None:
        if not self._learning:
            return
        preview = classify(self._learn_events)
        if [(c.address, c.kind, c.mode) for c in preview] != [(c.address, c.kind, c.mode) for c in self._learn_preview]:
            self._learn_preview = preview
            self.learnChanged.emit()

    # --- incoming MIDI (learning + live "turn to test") ----------------------------------------

    def _on_midi(self, name: str, msg: list[int]) -> None:
        if name != self._device:
            return
        if self._learning:
            self._learn_events.append((time.monotonic(), list(msg)))
            return
        prof = self._prof()
        addr = _address(msg)
        if not prof or addr is None:
            return
        i = prof.find(addr[0], addr[1], addr[2])
        if i is None:
            return
        c = prof.controls[i]
        what = decode(c, msg)
        if what is None:
            return
        kind, value = what
        if kind == "level":
            self.controlMoved.emit(i, float(value), f"{round(float(value) * 100)}%")
        elif kind == "step":
            self.controlMoved.emit(i, -1.0, f"{int(value):+d}")
        elif kind == "press":
            self.controlMoved.emit(i, 1.0, "pressed")
        else:
            self.controlMoved.emit(i, 0.0, "")

    # --- live desktop readings + service status ---------------------------------------------

    def _tick(self) -> None:
        self._read_status()
        if self._live_busy:
            return
        if self._device == LPD8:
            pa = self.cfg.actions(self._backend.currentSlot)
            items = [(f"k{i}", a) for i, a in enumerate(pa.knobs)] + [(f"p{i}", a) for i, a in enumerate(pa.pads)]
        else:
            prof = self._prof()
            items = [(f"c{i}", c.action) for i, c in enumerate(prof.controls)] if prof else []
        items = [(k, Action(a.type, a.target)) for k, a in items if a.type != "none"]
        if not items:
            if self._live:
                self._live = {}
                self._live_rev += 1
                self.liveChanged.emit()
            return
        self._live_busy = True
        targets = dict(self._targets)
        threading.Thread(target=lambda: self._liveReady.emit(_read_live(items, targets)), daemon=True).start()

    def _on_live(self, values: dict) -> None:
        self._live_busy = False
        if values != self._live:
            self._live = values
            self._live_rev += 1
            self.liveChanged.emit()

    def _read_status(self) -> None:
        try:
            st = json.loads(STATUS_PATH.read_text())
        except (OSError, ValueError):
            return
        last = st.get("last") or {}
        text, when = last.get("text", ""), float(last.get("time", 0))
        paused = bool(st.get("paused"))
        if paused != self._paused:
            self._paused = paused
            self.serviceChanged.emit()
        self._targets = st.get("targets") or {}
        devices = st.get("devices") or {}
        if devices != self._status_devices:
            self._status_devices = devices
            self.deviceChanged.emit()
        if (text, when) != (self._last_action, self._last_time):
            self._last_action, self._last_time = text, when
            self.lastActionChanged.emit()

    def _service_pid_alive(self) -> bool:
        try:
            pid = int(json.loads(STATUS_PATH.read_text()).get("pid", 0))
            os.kill(pid, 0)
            return pid > 0
        except (OSError, ValueError, TypeError):
            return False

    def _refresh_service(self) -> None:
        running = unit.active() or self._service_pid_alive()
        if running != self._running:
            self._running = running
            self.serviceChanged.emit()

    @Slot(bool)
    def setServiceEnabled(self, on: bool) -> None:
        self._save_timer.stop()
        self._save()
        err = unit.enable() if on else unit.disable()
        self._refresh_service()
        if err:
            self.notify.emit(f"Couldn't {'start' if on else 'stop'} desktop control: {err}", "error")
        elif on:
            self.notify.emit("Desktop control is on, and starts with your session", "positive")
        else:
            self.notify.emit("Desktop control is off", "info")

    @Slot(result=float)
    def now(self) -> float:
        return time.time()
