"""Thin wrappers over the desktop: PipeWire (wpctl/pw-dump), KDE D-Bus (busctl), app launching.

Everything that changes something is fire-and-forget (non-blocking) so knob
sweeps never stall the MIDI loop; only state reads wait, with short timeouts.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import audio

_children: list[subprocess.Popen] = []


def spawn(argv: list[str], detach: bool = False) -> None:
    """Start a process without waiting. Finished children are reaped on the next call."""
    _children[:] = [c for c in _children if c.poll() is None]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=detach,
        )
    except OSError:
        return
    if not detach:
        _children.append(proc)


def spawn_app(argv: list[str], name: str) -> None:
    """Start something for the user (an app, a command, the editor) in its own systemd scope,
    like Plasma does. Otherwise it would live inside chimp.service: restarting Chimp would
    kill it, and its output would land in Chimp's log."""
    if shutil.which("systemd-run"):
        unit = "app-" + re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:60] + "-" + secrets.token_hex(4) + ".scope"
        argv = [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            "--slice=app.slice",
            f"--unit={unit}",
            "--",
            *argv,
        ]
    spawn(argv, detach=True)


def run(argv: list[str], timeout: float = 1.5) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def busctl_call(service: str, path: str, iface: str, method: str, sig: str = "", *args) -> None:
    """Fire-and-forget session-bus call. Int/string arguments go over QtDBus in-process;
    anything else (e.g. unsigned ints) through busctl."""
    if set(sig) <= {"i", "s"} and _qt_call(service, path, iface, method, sig, args):
        return
    argv = ["busctl", "--user", "call", service, path, iface, method]
    if sig:
        argv += [sig, *map(str, args)]
    spawn(argv)


def _qt_call(service, path, iface, method, sig, args) -> bool:
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtDBus import QDBusConnection, QDBusMessage
    except ImportError:
        return False
    if QCoreApplication.instance() is None:
        return False
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    msg = QDBusMessage.createMethodCall(service, path, iface, method)
    msg.setArguments([int(a) if t == "i" else str(a) for t, a in zip(sig, args, strict=True)])
    return bool(bus.send(msg))


# --- PipeWire ----------------------------------------------------------------

SINK = "@DEFAULT_AUDIO_SINK@"
SOURCE = "@DEFAULT_AUDIO_SOURCE@"
_PULSE_NAMES = {SINK: "sink", SOURCE: "source"}


def _pulse_node(node: str | int):
    return _PULSE_NAMES.get(node, node) if isinstance(node, str) else node


def set_volume(node: str | int, fraction: float) -> None:
    if audio.set_volume(_pulse_node(node), fraction):
        return
    spawn(["wpctl", "set-volume", str(node), f"{max(0.0, min(fraction, 1.5)):.3f}"])


def change_volume(node: str | int, delta: float) -> float | None:
    """Nudge a volume by delta (0.02 = +2%), capped at 100%; returns the new volume."""
    new = audio.change_volume(_pulse_node(node), delta)
    if new is not None:
        return new
    change = f"{abs(delta) * 100:.0f}%{'+' if delta > 0 else '-'}"
    run(["wpctl", "set-volume", "-l", "1.0", str(node), change], timeout=1.0)
    return get_volume(node)[0]


def toggle_mute(node: str) -> None:
    if audio.toggle_mute(_pulse_node(node)):
        return
    spawn(["wpctl", "set-mute", node, "toggle"])


def get_volume(node: str | int) -> tuple[float | None, bool]:
    """(volume 0-1.5, muted) for a sink/source/stream, or (None, False) if unknown."""
    fast = audio.get_volume(_pulse_node(node))
    if fast is not None and fast[0] is not None:
        return fast
    out = run(["wpctl", "get-volume", str(node)], timeout=1.0)
    m = re.search(r"Volume: ([\d.]+)", out)
    return (float(m.group(1)) if m else None), "[MUTED]" in out


def is_muted(node: str) -> bool | None:
    vol, muted = get_volume(node)
    return None if vol is None else muted


_streams_cache: tuple[float, list[dict]] = (0.0, [])


def audio_streams(max_age: float = 2.0) -> list[dict]:
    """Playback streams currently in PipeWire: [{id, name, binary}]."""
    global _streams_cache
    stamp, streams = _streams_cache
    if time.monotonic() - stamp < max_age:
        return streams
    streams = []
    try:
        objs = json.loads(run(["pw-dump"], timeout=2.0) or "[]")
    except ValueError:
        objs = []
    for o in objs:
        props = (o.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        # Filter chains (HeSuVi, EasyEffects...) also create output streams; they aren't apps.
        if not (props.get("application.name") or props.get("application.process.binary")):
            continue
        try:
            pid = int(props.get("application.process.id") or 0)
        except (TypeError, ValueError):
            pid = 0
        streams.append(
            {
                "id": o.get("id"),
                "name": props.get("application.name") or props.get("node.name") or "",
                "binary": props.get("application.process.binary") or "",
                "node": props.get("node.name") or "",
                "pid": pid,
                "running": (o.get("info") or {}).get("state") == "running",
            }
        )
    _streams_cache = (time.monotonic(), streams)
    return streams


def streams_matching(target: str) -> list[dict]:
    t = target.strip().lower()
    if not t:
        return []
    return [s for s in audio_streams() if t in s["name"].lower() or t in s["binary"].lower()]


def _ancestors(pid: int, depth: int = 12) -> list[int]:
    out = []
    for _ in range(depth):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1:
            break
        out.append(pid)
    return out


_game_cache: dict[int, bool] = {}
_GAME_MARKERS = (b"wine", b"proton", b".exe", b"steamapps/common")


def looks_like_game(pid: int, app_class: str = "") -> bool:
    """Is this window's process a game? Steam/Proton/Wine/gamescope say so; a fullscreen
    browser or video player doesn't. Cached per pid while the process stays alive."""
    if pid <= 0:
        return False
    cached = _game_cache.get(pid)
    if cached is not None and pid_alive(pid):
        return cached
    cls = app_class.lower()
    result = cls.startswith("steam_app_") or cls == "gamescope"
    if not result:
        try:
            env = Path(f"/proc/{pid}/environ").read_bytes()
            result = any(k in env for k in _STEAM_ID_KEYS)
        except OSError:
            pass
    if not result:
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().lower()
            result = any(m in cmd for m in _GAME_MARKERS)
        except OSError:
            pass
    _game_cache[pid] = result
    return result


def proc_name(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def pid_alive(pid: int) -> bool:
    return pid > 0 and Path(f"/proc/{pid}").exists()


_STEAM_ID_KEYS = (b"SteamAppId=", b"SteamGameId=", b"STEAM_COMPAT_APP_ID=")


def steam_app_id(pid: int, app_class: str = "") -> str:
    """The Steam app id a process was launched with ("" if none). Every process of a
    Proton game has it, whichever of them owns the window, the network or the audio."""
    if app_class:
        m = re.search(r"steam_app_(\d+)", app_class.lower())
        if m:
            return m.group(1)
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return ""
    for key in _STEAM_ID_KEYS:
        for entry in env:
            if entry.startswith(key):
                value = entry[len(key) :].decode(errors="replace")
                if value and value != "0":
                    return value
    return ""


def streams_for_pid(pid: int, app_class: str = "") -> list[dict]:
    """Playback streams from this process, its children (games often play audio from a
    child), or, for a Steam/Proton game, any process launched as the same Steam app (under
    Proton the window's and the audio's processes can be siblings, not parent and child)."""
    if pid <= 0:
        return []
    streams = audio_streams()
    exact = [s for s in streams if s["pid"] == pid]
    if exact:
        return exact
    kids = [s for s in streams if s["pid"] and pid in _ancestors(s["pid"])]
    if kids:
        return kids
    try:
        app = steam_app_id(pid, app_class)
    except TypeError:
        app = steam_app_id(pid)
    if app:
        same_app = [s for s in streams if s["pid"] and steam_app_id(s["pid"]) == app]
        if same_app:
            return same_app
    name = proc_name(pid).lower()
    return [
        s
        for s in streams
        if name and (s["binary"].lower().startswith(name) or name.startswith(s["binary"].lower() or "\0"))
    ]


def playing_groups() -> list[tuple[str, list[dict]]]:
    """Apps currently making sound, oldest stream first: [(label, streams)]."""
    groups: dict[str, list[dict]] = {}
    for s in sorted(audio_streams(max_age=1.0), key=lambda s: s["id"] or 0):
        if not s["running"]:
            continue
        key = s["binary"] or s["name"]
        groups.setdefault(key, []).append(s)
    return [(v[0]["name"] or k, v) for k, v in groups.items()]


STREAM_ACTIONS = {"app_volume", "game_volume", "focused_volume", "playing_volume"}


def resolve_streams(a_type: str, target: str, targets: dict) -> tuple[list[dict], str]:
    """(streams, label) for a per-app volume action. `targets` = {"game": {...}, "focused": {...}}."""
    if a_type == "app_volume":
        streams = streams_matching(target)
        return streams, (streams[0]["name"] if streams else f"{target} (not playing)")
    if a_type in ("game_volume", "focused_volume"):
        t = (targets or {}).get("game" if a_type == "game_volume" else "focused")
        if not t:
            return [], ("No game running" if a_type == "game_volume" else "No focused app")
        streams = streams_for_pid(int(t["pid"]), app_class=str(t.get("name") or ""))
        name = t.get("name") or (streams[0]["name"] if streams else "?")
        return streams, (name if streams else f"{name} (no sound)")
    if a_type == "playing_volume":
        try:
            n = max(1, int(target or 1))
        except ValueError:
            n = 1
        groups = playing_groups()
        if n <= len(groups):
            label, streams = groups[n - 1]
            return streams, label
        return [], f"Slot {n}: nothing playing"
    return [], ""


def app_names() -> list[str]:
    names = {s["binary"] or s["name"] for s in audio_streams(max_age=0) if (s["binary"] or s["name"])}
    names.discard("")
    return sorted(names, key=str.lower)


# --- Plasma ------------------------------------------------------------------

OSD = ("org.kde.plasmashell", "/org/kde/osdService", "org.kde.osdService")


def osd_volume(percent: int) -> None:
    busctl_call(*OSD, "volumeChanged", "i", percent)


def osd_mic(percent: int) -> None:
    busctl_call(*OSD, "microphoneVolumeChanged", "i", percent)


def osd_text(icon: str, text: str) -> None:
    busctl_call(*OSD, "showText", "ss", icon, text)


def component_path(component: str) -> str:
    return "/component/" + re.sub(r"[^A-Za-z0-9_]", "_", component)


def invoke_shortcut(component: str, name: str) -> None:
    busctl_call(
        "org.kde.kglobalaccel", component_path(component), "org.kde.kglobalaccel.Component", "invokeShortcut", "s", name
    )


def list_shortcuts() -> list[dict]:
    """All registered KDE global shortcuts: [{value: 'component/name', label: 'Friendly - Action'}]."""
    out = run(
        [
            "busctl",
            "--user",
            "--json=short",
            "call",
            "org.kde.kglobalaccel",
            "/kglobalaccel",
            "org.kde.KGlobalAccel",
            "allComponents",
        ]
    )
    try:
        paths = json.loads(out)["data"][0]
    except (ValueError, KeyError, IndexError):
        return []
    result = []
    for path in paths:
        info = run(
            [
                "busctl",
                "--user",
                "--json=short",
                "call",
                "org.kde.kglobalaccel",
                path,
                "org.kde.kglobalaccel.Component",
                "allShortcutInfos",
            ]
        )
        try:
            rows = json.loads(info)["data"][0]
        except (ValueError, KeyError, IndexError):
            continue
        for name, friendly, comp, comp_friendly, *_ in rows:
            if not name or name.startswith("_k_"):
                continue
            result.append({"value": f"{comp}/{name}", "label": f"{comp_friendly or comp} – {friendly or name}"})
    result.sort(key=lambda r: r["label"].lower())
    return result


_displays_cache: tuple[float, list[dict]] = (0.0, [])
BRIGHTNESS = ("org.kde.ScreenBrightness", "/org/kde/ScreenBrightness")


def _prop(path: str, iface: str, prop: str):
    out = run(["busctl", "--user", "--json=short", "get-property", BRIGHTNESS[0], path, iface, prop])
    try:
        return json.loads(out)["data"]
    except (ValueError, KeyError):
        return None


def displays(max_age: float = 30.0) -> list[dict]:
    """[{path, label, max}] for every display Plasma can set brightness on."""
    global _displays_cache
    stamp, cached = _displays_cache
    if cached and time.monotonic() - stamp < max_age:
        return cached
    names = _prop(BRIGHTNESS[1], "org.kde.ScreenBrightness", "DisplaysDBusNames") or []
    found = []
    for n in names:
        path = f"{BRIGHTNESS[1]}/{n}"
        iface = "org.kde.ScreenBrightness.Display"
        found.append(
            {
                "path": path,
                "label": _prop(path, iface, "Label") or n,
                "max": int(_prop(path, iface, "MaxBrightness") or 100),
            }
        )
    _displays_cache = (time.monotonic(), found)
    return found


def get_brightness(target: str) -> float | None:
    """Brightness 0-1 of the first display matching target ("all" = first display)."""
    t = target.strip().lower()
    for d in displays():
        if t in ("", "all") or t in d["label"].lower():
            value = _prop(d["path"], "org.kde.ScreenBrightness.Display", "Brightness")
            return None if value is None else int(value) / max(d["max"], 1)
    return None


def set_brightness(target: str, fraction: float) -> None:
    t = target.strip().lower()
    for d in displays():
        if t in ("", "all") or t in d["label"].lower():
            value = round(max(0.0, min(fraction, 1.0)) * d["max"])
            # flags 0 = let Plasma show its own brightness OSD
            busctl_call(BRIGHTNESS[0], d["path"], "org.kde.ScreenBrightness.Display", "SetBrightness", "iu", value, 0)


# --- launching -----------------------------------------------------------------


def _desktop_file_exists(app_id: str) -> bool:
    dirs = [os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))]
    dirs += os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    dirs += [str(Path.home() / ".local/share/flatpak/exports/share"), "/var/lib/flatpak/exports/share"]
    return any((Path(d) / "applications" / f"{app_id}.desktop").exists() for d in dirs)


def launch(target: str) -> None:
    target = target.strip()
    if not target:
        return
    app_id = target.removesuffix(".desktop")
    if " " not in app_id and _desktop_file_exists(app_id):
        spawn_app(["gtk-launch", app_id], app_id)
    else:
        run_command(target)


def run_command(command: str, **values) -> None:
    if not command.strip():
        return
    for k, v in values.items():
        command = command.replace("{" + k + "}", shlex.quote(str(v)))
    spawn_app(["sh", "-c", command], "chimp-command")
