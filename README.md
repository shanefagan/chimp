# Chimp

Chimp turns MIDI controllers into a control deck for your KDE Plasma desktop, plus a native
replacement for Akai's LPD8 mk2 editor. PySide6 + Kirigami, python-rtmidi (ALSA sequencer).

## Install / update (Arch Linux / CachyOS)

    ./install.sh            # builds the pacman package, installs it (sudo), sets up the service
    ./install.sh --service  # same, and turn desktop control on without asking
    ./install.sh --uninstall

It builds `packaging/arch/PKGBUILD` with makepkg (pulling in python-build, python-installer,
python-hatchling and python-pytest as build dependencies), runs the tests during the
build, installs the package, offers `python-pulsectl` (recommended: in-process volume
control and event-driven pad lights; without it `wpctl` is used), removes any
from-source launcher/unit left over, and enables `chimp.service` for your
user. Re-run it after updates. The package installs:

- `/usr/bin/chimp`, `/usr/bin/chimpd`
- `/usr/lib/systemd/user/chimp.service`
- `/usr/share/applications/chimp.desktop` ("Chimp") and its icon

## Development

    just dev      # uv venv on top of system PySide6/Kirigami + editable install + dev tools
    just run      # editor from the source tree        just service   # desktop service
    just check    # ruff (lint + format), bandit, pytest
    just bench    # knob-sweep benchmark: processes spawned and CPU
    just live     # hardware/desktop checks (LPD8 plugged in, Plasma session)
    just pkg      # build the package only

Python 3.12+, PySide6 and Kirigami from the system (the Kirigami QML plugins are built
against the system Qt), `python-rtmidi`, optional `pulsectl`. Build backend: hatchling.

## Editor

    ./chimp              # or "Chimp" in the app launcher
    ./chimp --program 4  # open on a given LPD8 program tab
    ./chimp --device "EP-133"  # open on another MIDI device

Reads all 4 programs when the LPD8 is plugged in. Edit pads (note / CC / program change /
channel / colours), knobs (CC / channel / min / max) and program settings, then **Send**
(writes and reads back to verify). The device only loads a sent program when you switch to
it: hold PROGRAM + pad 1–4.

## Other MIDI devices (KO II, MicroFreak, anything class-compliant)

Every controller on the system shows up in the **Device** bar at the top.

1. Pick the device and click **Learn controls**.
2. Move every knob and fader through its range, turn encoders both ways, press each button
   twice. The app works out what each control is from what it sends: knob or fader (absolute
   position), endless encoder ("64 ± n" or "1 / 127" style, changes volume/brightness in 2%
   steps), pitch-bend fader (14-bit), button (momentary or toggle). CC 32-63 shadowing a
   14-bit control are dropped, aftertouch is ignored. Fix any wrong guess per tile.
3. Click **Done**, then pick an action per control. Tiles show the incoming MIDI ("move to
   test") and the live desktop value.

Profiles are separate sets of actions for the same controls ("Desktop", "Streaming", ...).
Switch them from the tray icon's menu or give a button **Next/Previous profile**.
**Use for desktop control** turns a whole device off (e.g. while playing the MicroFreak).

## LPD8 desktop control

Every pad and knob tile has an **action** picker at the bottom. A program with actions
controls the desktop whenever it is the active program on the LPD8; programs without
actions stay plain MIDI for music apps. Tabs with actions show "· Desktop". Out of the
box Program 4 has actions (master/mic/Brave/Discord volume, brightness, mutes, media).

- Turn it on with the **Desktop control** toolbar button (a systemd user unit, starts with
  your session). The bar under the header says whether it's working, and why not
  (e.g. "the LPD8 is on Program 1, which has no desktop actions"), plus the last action.
- Knob tiles show the live desktop value (volume, brightness) as a bar, read from
  PipeWire/Plasma every half second, so turning a knob visibly moves it.
- Pad tiles show live state (mic/speakers muted).
- Plasma's on-screen display pops up for volume/mute and when you switch programs on the LPD8.
- **Overlay**: while you use a controller, a see-through picture of it appears on screen
  (Plasma's overlay layer via layer-shell-qt, so it also shows over fullscreen games,
  never takes focus and lets clicks through) and fades out 2.5 s after you stop: the
  LPD8 laid out like the hardware, other devices as the current group's pads, with each
  control's action, live values and presses. It replaces Plasma's volume popups for
  knobs. Settings: on/off, position, "over fullscreen games" (editor and tray menu).
- The service puts an LPD8 icon in the system tray: full colour while it's controlling the
  desktop, greyed out otherwise. Hover for the program and last action, click to open the
  editor, right-click to pause/resume or quit until next login.
- "Colour pads by action" sets dim OFF colours and an ON colour per action; Send to apply.

Knobs: master volume, mic volume, per-app volume (PipeWire), screen brightness (Plasma,
incl. DDC monitors), or a command with `{value}`/`{percent}`/`{delta}`. Volume knobs that
follow what you're doing:

- **Game volume (auto)**: the game bpfrag sees in a match (its system-bus `GamePid`), else
  the fullscreen app in front, else the last fullscreen app while it's still running (so
  alt-tabbing out of a game keeps the knob on the game). Audio from the game's child
  processes counts too.
- **Focused app volume**: whichever window has focus.
- **Playing app (auto)**, slot 1, 2, 3...: the apps currently making sound, oldest first.
  Filter chains (HeSuVi, EasyEffects) are skipped. The popup names the app.

Window focus comes from a small KWin script the service loads (`chimp-focus`),
which reports activations and fullscreen changes to `org.chimp.Service` on the session bus.
There's no separate voice-chat knob: your own voice is the mic knob, and in-game voice comes
out of the game's own audio stream.
Pads: mute mic/speakers, media keys, launch app, any KDE global shortcut, shell command,
OBS scene / record / stream / source mute (obs-websocket v5; password read from OBS).

Logs: `journalctl --user -u chimpd -f`.
Config: `~/.config/chimp/desktop.json`. The factory Program 4 was backed up to
`~/.local/share/chimp/presets/program-4-before-desktop.json` (Open… to restore).

## Tests

    .venv/bin/python -m pytest -q                              # offline (protocol, config, learning)
    PYTHONPATH=. .venv/bin/python tests/live_check.py          # read-only, needs the LPD8
    PYTHONPATH=. .venv/bin/python tests/live_write_check.py    # writes a change, restores it
    PYTHONPATH=. .venv/bin/python tests/live_desktop_actions.py  # simulated presses, undone
