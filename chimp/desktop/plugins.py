"""Your own pad actions, from Python files in ~/.local/share/chimp/plugins/.

Chimp ships the actions everyone can use; things only your machine has (a script, an
app of your own on the session bus) go in a plugin, which stays out of Chimp's code.
A plugin is one file, `<name>.py`, or a folder `<name>/` with an `__init__.py`:

    ACTIONS = {
        "lights_hold": {
            "label": "Desk lights: on while held",       # in the editor's action list
            "short": "Lights (hold)",                    # on the overlay (default: label)
            "field": "",                                 # the target field's name, if any
            "hint": "",                                  # and its placeholder
            "hold": True,                                # release() when the pad is let go
            "colour": [0, 160, 255],                     # the pad's light when it has this
        },
    }

    def press(action: str, target: str) -> None: ...     # required
    def release(action: str, target: str) -> None: ...   # for "hold" actions
    def marker_note() -> str: ...                        # optional: added to markers' labels
                                                         # (e.g. where a teleprompter is)

Plugins are read lazily: ACTIONS is read from the file's source without running it (it
must be a plain literal), so the editor and the service list your actions without
importing anything; the module is imported the first time one of its functions is
needed, in the service only. A plugin that fails is reported in the log and skipped;
Chimp carries on. Action ids can't replace Chimp's own.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


def plugin_dir() -> Path:
    from .config import DATA_DIR  # (config imports this module)

    return Path(os.environ.get("CHIMP_PLUGINS_DIR") or DATA_DIR / "plugins")


@dataclass
class PluginAction:
    id: str
    label: str
    short: str = ""
    field: str = ""
    hint: str = ""
    hold: bool = False
    colour: tuple[int, int, int] | None = None


@dataclass
class Plugin:
    name: str
    path: Path  # the .py file (a package's __init__.py)
    actions: dict[str, PluginAction] = field(default_factory=dict)
    functions: set[str] = field(default_factory=set)  # top-level defs, read from the source
    module: ModuleType | None = None
    failed: bool = False


_plugins: dict[str, Plugin] | None = None  # name -> plugin, once discovered
_by_action: dict[str, Plugin] = {}


def _warn(text: str) -> None:
    print(f"plugins: {text}", file=sys.stderr, flush=True)


def _files(folder: Path) -> list[tuple[str, Path]]:
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.iterdir()):
        if p.name.startswith(("_", ".")):
            continue
        if p.is_file() and p.suffix == ".py":
            out.append((p.stem, p))
        elif p.is_dir() and (p / "__init__.py").is_file():
            out.append((p.name, p / "__init__.py"))
    return out


def _colour(v) -> tuple[int, int, int] | None:
    if isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in v):
        return (v[0], v[1], v[2])
    return None


def _read(name: str, path: Path, taken: set[str]) -> Plugin | None:
    """The plugin's ACTIONS and function names, from its source: nothing is run."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, SyntaxError, ValueError) as exc:
        _warn(f"{path}: {exc}")
        return None
    raw = None
    functions = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "ACTIONS" for t in targets) and node.value is not None:
            try:
                raw = ast.literal_eval(node.value)
            except ValueError:
                _warn(f"{path}: ACTIONS must be a plain dict literal")
                return None
    if not isinstance(raw, dict):
        _warn(f"{path}: no ACTIONS dict")
        return None
    if "press" not in functions:
        _warn(f"{path}: no press(action, target) function")
        return None
    plugin = Plugin(name, path, functions=functions)
    for aid, spec in raw.items():
        if not isinstance(aid, str) or not aid.isidentifier() or not isinstance(spec, dict):
            _warn(f"{path}: skipping action {aid!r} (ids are identifiers, specs are dicts)")
            continue
        if aid in taken:
            _warn(f"{path}: action {aid} is already taken, skipped")
            continue
        label = spec.get("label")
        if not isinstance(label, str) or not label.strip():
            _warn(f"{path}: action {aid} has no label, skipped")
            continue

        def text(key: str, s=spec) -> str:
            v = s.get(key, "")
            return v if isinstance(v, str) else ""

        plugin.actions[aid] = PluginAction(
            aid,
            label.strip(),
            short=text("short") or label.strip(),
            field=text("field"),
            hint=text("hint"),
            hold=spec.get("hold") is True,
            colour=_colour(spec.get("colour")),
        )
        taken.add(aid)
    return plugin


def discover(builtin: set[str] | frozenset[str] = frozenset(), folder: Path | None = None) -> dict[str, Plugin]:
    """Find the plugins (once; again after reset()). `builtin` ids can't be taken."""
    global _plugins
    if _plugins is not None:
        return _plugins
    found: dict[str, Plugin] = {}
    taken = set(builtin)
    for name, path in _files(folder or plugin_dir()):
        plugin = _read(name, path, taken)
        if plugin is not None and plugin.actions:
            found[name] = plugin
    _plugins = found
    _by_action.clear()
    for plugin in found.values():
        for aid in plugin.actions:
            _by_action[aid] = plugin
    return found


def reset() -> None:
    """Forget what was found (tests, or a rescan); loaded modules are dropped too."""
    global _plugins
    _plugins = None
    _by_action.clear()


def actions() -> dict[str, PluginAction]:
    return {aid: p.actions[aid] for aid, p in _by_action.items()}


def handles(action: str) -> bool:
    return action in _by_action


def _module(plugin: Plugin) -> ModuleType | None:
    if plugin.module is not None or plugin.failed:
        return plugin.module
    try:
        spec = importlib.util.spec_from_file_location(
            f"chimp_plugin_{plugin.name}",
            plugin.path,
            submodule_search_locations=[str(plugin.path.parent)] if plugin.path.name == "__init__.py" else None,
        )
        if spec is None or spec.loader is None:
            raise ImportError("can't load")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - a broken plugin mustn't take Chimp down
        plugin.failed = True
        _warn(f"{plugin.name} failed to load:\n{traceback.format_exc().rstrip()}")
        return None
    plugin.module = module
    return module


def _call(plugin: Plugin, fn: str, *args):
    module = _module(plugin)
    func = getattr(module, fn, None) if module else None
    if not callable(func):
        return None
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 - nor a failing one
        _warn(f"{plugin.name}.{fn} failed:\n{traceback.format_exc().rstrip()}")
        return None


def press(action: str, target: str) -> None:
    plugin = _by_action.get(action)
    if plugin is not None:
        _call(plugin, "press", action, target)


def release(action: str, target: str) -> None:
    plugin = _by_action.get(action)
    if plugin is not None and plugin.actions[action].hold and "release" in plugin.functions:
        _call(plugin, "release", action, target)


def marker_note() -> str:
    """Notes from plugins with a marker_note() (e.g. where a teleprompter is), joined."""
    notes = []
    for plugin in (_plugins or {}).values():
        if "marker_note" in plugin.functions:
            note = _call(plugin, "marker_note")
            if isinstance(note, str) and note.strip():
                notes.append(note.strip())
    return " · ".join(notes)
