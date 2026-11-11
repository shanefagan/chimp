"""Desktop-control configuration: which action each pad and knob triggers, per program.

Any of the LPD8's 4 programs can carry actions. Whichever program is active on the
device decides what the pads and knobs do; programs without actions are plain MIDI.

Stored as JSON at ~/.config/chimp/desktop.json. The background service
watches the file and reloads it, so edits in the editor apply immediately.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import protocol as p

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CONFIG_DIR = _CONFIG_HOME / "chimp"
CONFIG_PATH = CONFIG_DIR / "desktop.json"
DATA_DIR = _DATA_HOME / "chimp"
# Earlier names of this app ("knobdeck" briefly, "lpd8mk2-editor" before that): their
# settings and presets are copied over once, newest first.
LEGACY_NAMES = ("knobdeck", "lpd8mk2-editor")
LEGACY_DIRS = tuple(
    (home / old, new) for home, new in ((_CONFIG_HOME, CONFIG_DIR), (_DATA_HOME, DATA_DIR)) for old in LEGACY_NAMES
)


def migrate_legacy() -> list[str]:
    """Copy settings/presets from the pre-rename folders if the new ones don't exist yet."""
    import shutil

    copied = []
    for old, new in LEGACY_DIRS:
        if old.is_dir() and not new.exists():  # first match wins: later legacy dirs are skipped
            try:
                shutil.copytree(old, new)
                copied.append(f"{old} -> {new}")
            except OSError:
                pass
    return copied


# type id -> (label, what the "target" field means, placeholder/hint)
KNOB_ACTIONS: dict[str, tuple[str, str, str]] = {
    "none": ("No action", "", ""),
    "master_volume": ("Master volume", "", ""),
    "app_volume": ("App volume", "App name", "e.g. brave, discord, spotify"),
    "game_volume": ("Game volume (auto)", "", ""),
    "focused_volume": ("Focused app volume", "", ""),
    "playing_volume": ("Playing app (auto)", "Slot", "1 = first app making sound, 2 = second, ..."),
    "mic_volume": ("Microphone volume", "", ""),
    "brightness": ("Screen brightness", "Display", "all, or part of the monitor name"),
    "command": ("Run command", "Command", "{value} = 0-127, {percent} = 0-100, {delta} = encoder steps"),
}

PAD_ACTIONS: dict[str, tuple[str, str, str]] = {
    "none": ("No action", "", ""),
    "mic_mute": ("Mute microphone", "", ""),
    "output_mute": ("Mute speakers", "", ""),
    "media": ("Media control", "Action", "playpause, next, previous, stop"),
    "launch": ("Launch app", "App", "desktop file id (org.kde.dolphin) or a command"),
    "shortcut": ("KDE shortcut", "Shortcut", "component/name, e.g. kwin/Overview"),
    "command": ("Run command", "Command", "any shell command"),
    "obs_scene": ("OBS: switch scene", "Scene", "scene name"),
    "obs_record": ("OBS: toggle recording", "", ""),
    "obs_stream": ("OBS: toggle streaming", "", ""),
    "obs_mute": ("OBS: toggle source mute", "Source", "audio source name, e.g. Mic/Aux"),
    "marker": ("Add marker", "Note", "optional, e.g. retake (saved with the time, for cutting later)"),
    "sound": ("Play sound", "Sound file", "a WAV, MP3, OGG, FLAC... file"),
    "sound_stop": ("Stop all sounds", "", ""),
}

# Pad actions that also act when the pad is let go (the pad must be momentary).
HOLD_ACTIONS: set[str] = set()

# Buttons on generic devices can also flip between that device's profiles.
BUTTON_ACTIONS: dict[str, tuple[str, str, str]] = {
    **PAD_ACTIONS,
    "profile_next": ("Next profile", "", ""),
    "profile_prev": ("Previous profile", "", ""),
}

CONTROL_KINDS = ("knob", "fader", "encoder", "button")
MSG_TYPES = ("cc", "note", "pb", "pc", "rt")  # rt = start/continue/stop
# absolute = position 0-127 (pb: 0-16383); rel64: 64 +/- n; rel2c: 1..63 up, 65..127 down
MODES = ("absolute", "rel64", "rel2c", "momentary", "toggle")

MEDIA_SHORTCUTS = {
    "playpause": "playpausemedia",
    "play": "playmedia",
    "pause": "pausemedia",
    "next": "nextmedia",
    "previous": "previousmedia",
    "stop": "stopmedia",
}

OVERLAY_POSITIONS = ("bottom", "top", "bottom-right", "top-right")

# Pads whose LED reflects on/off state
STATEFUL_PADS = {"mic_mute", "output_mute", "obs_scene", "obs_record", "obs_stream", "obs_mute", "sound"}


@dataclass
class Action:
    type: str = "none"
    target: str = ""
    # settings beyond the target, for actions that have them ("Play sound": mode,
    # output, volume); plain JSON values only
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {"type": self.type, "target": self.target}
        if self.options:
            d["options"] = dict(self.options)
        return d

    @classmethod
    def from_dict(cls, d) -> Action:
        if not isinstance(d, dict):
            return cls()
        raw = d.get("options")
        options = {
            str(k)[:40]: v
            for k, v in (raw.items() if isinstance(raw, dict) else [])
            if isinstance(v, (str, int, float, bool)) and not (isinstance(v, str) and len(v) > 500)
        }
        return cls(str(d.get("type", "none")), str(d.get("target", "")), options)


@dataclass
class ObsSettings:
    host: str = "localhost"
    port: int = 4455
    password: str = ""  # blank = read it from OBS's own websocket config


def _none_knobs() -> list[Action]:
    return [Action() for _ in range(p.NUM_KNOBS)]


def _none_pads() -> list[Action]:
    return [Action() for _ in range(p.NUM_PADS)]


def default_knobs() -> list[Action]:
    return [
        Action("master_volume"),
        Action("mic_volume"),
        Action("app_volume", "brave"),
        Action("app_volume", "discord"),
        Action("brightness", "all"),
        Action(),
        Action(),
        Action(),
    ]


def default_pads() -> list[Action]:
    # Pads 1-4 are the bottom row on the hardware, 5-8 the top row.
    return [
        Action("mic_mute"),
        Action("output_mute"),
        Action("media", "previous"),
        Action("media", "playpause"),
        Action("media", "next"),
        Action("shortcut", "kwin/Overview"),
        Action("obs_record"),
        Action("launch", "com.mitchellh.ghostty"),
    ]


@dataclass
class ProgramActions:
    knobs: list[Action] = field(default_factory=_none_knobs)
    pads: list[Action] = field(default_factory=_none_pads)

    def active(self) -> bool:
        return any(a.type != "none" for a in self.knobs + self.pads)

    def to_dict(self) -> dict:
        return {"knobs": [a.to_dict() for a in self.knobs], "pads": [a.to_dict() for a in self.pads]}


@dataclass
class Control:
    """One learned control on a generic MIDI device."""

    name: str
    kind: str  # knob | fader | encoder | button
    msg: str  # cc | note | pb | pc
    channel: int  # 1-16
    number: int = 0  # CC/note/program number (unused for pb)
    mode: str = "absolute"
    action: Action = field(default_factory=Action)
    group: str = ""  # display group/bank ("Group A"); doesn't affect matching

    @property
    def address(self) -> tuple[str, int, int]:
        return (self.msg, self.channel, 0 if self.msg == "pb" else self.number)

    def describe(self) -> str:
        where = {
            "cc": f"CC {self.number}",
            "note": f"note {self.number}",
            "pb": "pitch bend",
            "pc": f"program {self.number}",
            "rt": {0xFA: "Start", 0xFB: "Continue", 0xFC: "Stop"}.get(self.number, "transport"),
        }[self.msg]
        return where if self.msg == "rt" else f"{where} · ch {self.channel}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "msg": self.msg,
            "channel": self.channel,
            "number": self.number,
            "mode": self.mode,
            "action": self.action.to_dict(),
            **({"group": self.group} if self.group else {}),
        }

    @classmethod
    def from_dict(cls, d) -> Control | None:
        try:
            c = cls(
                str(d["name"]),
                str(d["kind"]),
                str(d["msg"]),
                int(d["channel"]),
                int(d.get("number", 0)),
                str(d.get("mode", "absolute")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if c.kind not in CONTROL_KINDS or c.msg not in MSG_TYPES or c.mode not in MODES or not 1 <= c.channel <= 16:
            return None
        act = d.get("action") if isinstance(d.get("action"), dict) else {}
        a = Action.from_dict(act)
        valid = BUTTON_ACTIONS if c.kind == "button" else KNOB_ACTIONS
        c.action = a if a.type in valid or (c.kind == "button" and _plugin_shaped(a.type)) else Action()
        c.group = str(d.get("group", ""))[:40]
        return c


@dataclass
class Profile:
    name: str = "Default"
    controls: list[Control] = field(default_factory=list)

    def find(self, msg: str, channel: int, number: int) -> int | None:
        key = (msg, channel, 0 if msg == "pb" else number)
        for i, c in enumerate(self.controls):
            if c.address == key:
                return i
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "controls": [c.to_dict() for c in self.controls]}


@dataclass
class DeviceConfig:
    """A generic MIDI controller, identified by its ALSA client name."""

    enabled: bool = True
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "profiles": [p.to_dict() for p in self.profiles]}

    @classmethod
    def from_dict(cls, d) -> DeviceConfig:
        if not isinstance(d, dict):
            return cls()
        profiles = []
        for pd in d.get("profiles") or []:
            if not isinstance(pd, dict):
                continue
            controls = [c for c in (Control.from_dict(x) for x in pd.get("controls") or []) if c]
            profiles.append(Profile(str(pd.get("name", "Profile")), controls))
        return cls(bool(d.get("enabled", True)), profiles or [Profile()])


def _default_programs() -> dict[int, ProgramActions]:
    progs = {slot: ProgramActions() for slot in range(1, p.NUM_PROGRAMS + 1)}
    progs[4] = ProgramActions(default_knobs(), default_pads())
    return progs


def _plugin_shaped(t: str) -> bool:
    """An action type that could be a plugin's whose plugin isn't here right now: kept
    (it does nothing) so that saving the config doesn't wipe the pad."""
    return t.isidentifier() and t not in KNOB_ACTIONS


def _parse_actions(items, n: int, valid: dict, fallback) -> list[Action]:
    if not isinstance(items, list):
        return fallback()
    out = []
    for it in items[:n]:
        a = Action.from_dict(it)
        out.append(a if a.type in valid or _plugin_shaped(a.type) else Action())
    return out + [Action() for _ in range(n - len(out))]


@dataclass
class DesktopConfig:
    osd: bool = True  # Plasma on-screen display for volume/mute/program changes
    leds: bool = True  # light pads to show mute/record state
    overlay: bool = True  # Chimp's on-screen overlay of the device while you use it
    overlay_position: str = "bottom"  # bottom | top | bottom-right | top-right
    overlay_over_games: bool = True  # also show it over fullscreen games
    programs: dict[int, ProgramActions] = field(default_factory=_default_programs)
    devices: dict[str, DeviceConfig] = field(default_factory=dict)
    obs: ObsSettings = field(default_factory=ObsSettings)

    def actions(self, slot: int) -> ProgramActions:
        return self.programs[slot]

    def slots_with_actions(self) -> list[int]:
        return [s for s, pa in sorted(self.programs.items()) if pa.active()]

    def uses_obs(self) -> bool:
        """Any OBS action, or a marker (which notes the time in OBS's recording)."""

        def obs(t: str) -> bool:
            return t.startswith("obs_") or t == "marker"

        lpd8 = any(obs(a.type) for pa in self.programs.values() for a in pa.pads)
        generic = any(obs(c.action.type) for d in self.devices.values() for prof in d.profiles for c in prof.controls)
        return lpd8 or generic

    def sound_targets(self) -> list[str]:
        """The files every "Play sound" action plays, LPD8 programs and devices alike."""
        out = [a.target for pa in self.programs.values() for a in pa.pads if a.type == "sound"]
        out += [
            c.action.target
            for d in self.devices.values()
            for prof in d.profiles
            for c in prof.controls
            if c.action.type == "sound"
        ]
        return [t for t in out if t.strip()]

    def obs_inputs(self) -> set[str]:
        names = {a.target for pa in self.programs.values() for a in pa.pads if a.type == "obs_mute"}
        names |= {
            c.action.target
            for d in self.devices.values()
            for prof in d.profiles
            for c in prof.controls
            if c.action.type == "obs_mute"
        }
        return {n for n in names if n}

    def to_dict(self) -> dict:
        return {
            "version": 3,
            "osd": self.osd,
            "leds": self.leds,
            "overlay": self.overlay,
            "overlay_position": self.overlay_position,
            "overlay_over_games": self.overlay_over_games,
            "programs": {str(s): pa.to_dict() for s, pa in sorted(self.programs.items())},
            "devices": {name: dev.to_dict() for name, dev in sorted(self.devices.items())},
            "obs": {"host": self.obs.host, "port": self.obs.port, "password": self.obs.password},
        }

    @classmethod
    def from_dict(cls, d: dict) -> DesktopConfig:
        if not isinstance(d, dict):
            return cls()
        progs = {slot: ProgramActions() for slot in range(1, p.NUM_PROGRAMS + 1)}
        raw = d.get("programs")
        if isinstance(raw, dict):
            for key, val in raw.items():
                try:
                    slot = int(key)
                except ValueError:
                    continue
                if slot in progs and isinstance(val, dict):
                    progs[slot] = ProgramActions(
                        _parse_actions(val.get("knobs"), p.NUM_KNOBS, KNOB_ACTIONS, _none_knobs),
                        _parse_actions(val.get("pads"), p.NUM_PADS, PAD_ACTIONS, _none_pads),
                    )
        elif "knobs" in d or "pads" in d:  # version 1: one desktop program
            try:
                slot = min(max(int(d.get("slot", 4)), 1), p.NUM_PROGRAMS)
            except (TypeError, ValueError):
                slot = 4
            progs[slot] = ProgramActions(
                _parse_actions(d.get("knobs"), p.NUM_KNOBS, KNOB_ACTIONS, default_knobs),
                _parse_actions(d.get("pads"), p.NUM_PADS, PAD_ACTIONS, default_pads),
            )
        else:
            progs = _default_programs()
        obs = d.get("obs") if isinstance(d.get("obs"), dict) else {}
        try:
            port = int(obs.get("port", 4455))
        except (TypeError, ValueError):
            port = 4455
        devices = {}
        if isinstance(d.get("devices"), dict):
            devices = {str(k): DeviceConfig.from_dict(v) for k, v in d["devices"].items()}
        return cls(
            osd=bool(d.get("osd", True)),
            leds=bool(d.get("leds", True)),
            overlay=bool(d.get("overlay", True)),
            overlay_position=str(d.get("overlay_position", "bottom"))
            if d.get("overlay_position") in OVERLAY_POSITIONS
            else "bottom",
            overlay_over_games=bool(d.get("overlay_over_games", True)),
            programs=progs,
            devices=devices,
            obs=ObsSettings(str(obs.get("host", "localhost")), port, str(obs.get("password", ""))),
        )

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> DesktopConfig:
        try:
            return cls.from_dict(json.loads(path.read_text()))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            return cls()

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        # owner-only: the OBS websocket password can be in here
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), indent=2) + "\n")
        tmp.replace(path)


# Highlight colour per action, used by "Light pads to match actions".
ON_COLOURS = {
    "mic_mute": (255, 0, 0),
    "output_mute": (255, 0, 0),
    "obs_record": (255, 0, 0),
    "obs_stream": (255, 0, 255),
    "obs_mute": (255, 80, 0),
    "obs_scene": (0, 255, 0),
    "media": (0, 255, 120),
    "marker": (255, 160, 0),
    "sound": (0, 200, 160),
}


def register_plugins() -> None:
    """Your plugins' pad actions (plugins.py), read from their source: nothing runs here."""
    from . import plugins

    builtin = set(PAD_ACTIONS) | set(BUTTON_ACTIONS) | set(KNOB_ACTIONS)
    for plugin in plugins.discover(builtin).values():
        for aid, pa in plugin.actions.items():
            PAD_ACTIONS[aid] = BUTTON_ACTIONS[aid] = (pa.label, pa.field, pa.hint)
            if pa.hold:
                HOLD_ACTIONS.add(aid)
            if pa.colour:
                ON_COLOURS[aid] = pa.colour


register_plugins()
