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

## Debian, Ubuntu and Fedora

Every push builds a `.deb` (Debian 13+, Ubuntu 25.04+: the first with PySide6 6.8 and
Plasma 6) and an `.rpm` (current Fedora), and every `vX.Y.Z` tag publishes them, with the
Arch package, as a GitHub release.

    sudo apt install ./chimp_*.deb      # or: sudo dnf install ./chimp-*.rpm
    systemctl --user enable --now chimp.service

Fedora has no `python3-rtmidi`, so the RPM builds python-rtmidi from its PyPI sdist into
`/usr/lib64/chimp/python`, which only Chimp puts on its path.

The packages don't enable the service for every user; the editor's **Desktop control**
switch does it for you, or run the `systemctl` line above.

## Development

    just dev      # uv venv on top of system PySide6/Kirigami + editable install + dev tools
    just run      # editor from the source tree        just service   # desktop service
    just check    # ruff (lint + format), bandit, pytest
    just bench    # knob-sweep benchmark: processes spawned and CPU
    just live     # hardware/desktop checks (LPD8 plugged in, Plasma session)
    just arch     # build the Arch package only
    just deb / just rpm      # Debian / Fedora packages (in a container of that distro)
    just bump 0.6.0 "note"   # set the version everywhere; then commit, tag v0.6.0, push

CI (`.github/workflows/ci.yml`) runs the same recipes: `check` on Debian 13, `deb`,
`deb-lint` and `deb-smoke` in Debian, `rpm`, `rpm-lint` and `rpm-smoke` in Fedora, `arch`
and `arch-smoke` in Arch. Each smoke test installs the package and runs
`chimp --check-install`, which imports every module and compiles every QML file
offscreen, so a missing dependency fails the build.

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

## Chimp Loop: a drum machine for any controller

`chimp-loop` (or "Chimp Loop" in the launcher and the tray menu) is a Volca Beats-style drum
machine: 16 parts, live looping and step entry on the same pattern, a sound per pad and a
master bus over everything.

**Pattern**
- **Record** (Space): play; press again to close the loop. Its length is your take, and the
  tempo is worked out from it (or pick 1–8 bars and it closes on its own, with a metronome).
  **Record** again overdubs a layer; **Undo** (Backspace) removes the last layer or step,
  **Clear** (Delete) starts over, **Play/Stop** (Enter).
- **Steps:** a 16-part × 16-step grid, a bar per page, with the playhead. Click a step on or
  off; right-click (or Shift-click) cycles soft / normal / accent. On an empty pattern the
  first step makes a loop of the chosen length (1 bar if Free). Recorded hits show on it too.
- **Quantise** (off, 1/4 to 1/32) is applied on playback, so it can change after recording.
  **Swing** pushes every second 1/8 or 1/16 late (up to 75%).
- **Velocity:** pads play and record as hard as you hit them; **Fixed velocity** plays every
  hit at full strength.

**Each pad's sound** (click a part name or hit the pad to select it)
- Level, pan, pitch (±24 semitones)
- Attack, sustain and decay
- Filter: low-, high- or band-pass with cutoff and resonance
- Drive: soft, hard clip, fuzz or crush (bits and sample rate)
- Punch (compression on the hit), reverse
- Delay and reverb sends: how much of this pad goes to the master delay and reverb

The shaping is rendered into the pad's sample in the background when a setting changes, so
playing costs nothing extra; level, pan and sends act at once.

**Master** (over everything, in this order)
- Delay: 1/16 to 1/2 (dotted, triplet), echoes, ping-pong
- Reverb: an 8-line feedback network, up to 30 s, with tone and modulation. Its modes:
  - **Hall**
  - **Rise:** the tail fed back an octave up
  - **Sink:** the tail fed back an octave down
  - **Reverse:** the tail in reversed grains that swell into each hit
- Drive (soft / hard / fuzz / crush)
- Filter: one knob, DJ style (centre off, left low-pass, right high-pass), with resonance
- Compressor (slow glue or fast punch), with the gain reduction shown
- Limiter: brick-wall at −0.3 dB, and "Limit" pushes up to +18 dB into it
- Volume

Presets for the master: **Clean**, **Abyss** (big, dark, octave-down shimmer, dotted
ping-pong, fuzz, glued and limited), **Bloom**, **Reverse swell**, **Crushed**.

**Knobs:** the LPD8's knobs 1–8 (CC 70–77) are on a page: **Pad** (level, pitch, attack,
decay, cutoff, resonance, drive, reverb send of the selected part) or **Master** (filter,
resonance, drive, delay, echoes, reverb, size, compress). The numbered dials show which. K
switches page, or a controller button assigned to it. Any dial can be given its own knob,
fader or gamepad axis instead (the small controller icon under it). Double-click a dial to
reset it. The selected part only changes when you choose it (click its name, right-click a
pad, Shift + its key, or hold **Select** + pad); playing pads never moves it.

**Performance**
- **Roll:** hold Roll (G, or a controller button) and hold pads: they repeat on the beat at
  1/8, 1/16, 1/16 triplet or 1/32, and record while recording.
- **Mute / solo** each part (M / S, Ctrl / Alt + its key, or hold Mute / Solo + pad).
  They affect the pattern; pads you play always sound.
- **Glitches**, held: stutter 1/4, 1/8, 1/16, 1/32 (the last slice on repeat), tape stop,
  reverse (the last beat backwards), half speed. Keys 5–0 and -, the buttons above the
  grid, or controller buttons.
- **Live bends, on purpose:** pitch acts on sounds already ringing (turn it during a crash
  for a dive-bomb), stepping a little each block; delay time glides like tape, so changing
  it warbles the echoes; reverb size stretches its lines, so turning it bends the tail.
- **Pads view:** big pads with mute and solo, next to the step view.

**Sounds:** save the whole sound (kit, every pad, the master) under a name, and switch
between saved sounds with the buttons, [ and ], or controller buttons ("Next/Previous saved
sound"). Kept in `~/.local/share/chimp/sounds/`.

**Gamepads and analog keyboards:** with python-evdev installed, gamepads are controllers
too: sticks and triggers can be given any parameter (sticks rest in the middle: spring-
loaded modulation; the filter's centre is "off"), buttons any sound or action. A Wooting
keyboard in gamepad mode shows up the same way, so analog key depth can drive a parameter.

**Pads and buttons:** notes 36–51 play sounds 1–16, so an LPD8 program on notes 36–43 plays
Kick, Snare, Clap, Hi-hat, Open hat, Rim, Tom, Crash, and a KO II group the first 12. **Assign
pads** maps any pad to any sound; **Controller buttons…** gives record, play, undo, clear,
quantise, metronome, next kit and knob page to buttons on a controller. Keys: Z X C V, A S D F,
Q W E R, 1 2 3 4 play the sounds as laid out on screen.

**Kits:** two are bundled (`drums1`, `drums2`). Add your own as a folder of WAVs in
`~/.local/share/chimp/kits/<name>/`. Files named `kick`, `snare_1`, `clap`, `hihat_1`,
`open_hihat`, `rim`, `tom_1`, `crash`, `snare_2`, `hihat_2`, `tom_2`, `tom_3`, `ride`,
`perc_1`, `perc_2` and `perc_3` go to those slots; others fill the gaps.

**Save** loops (`~/.local/share/chimp/loops/`, with every pad and master setting), and
**export** to `~/Music/Chimp/`:
- WAV, N repeats, with all of the sound, looping seamlessly
- MIDI: General MIDI drum notes on channel 10, at the loop's tempo, swing included

**Under the hood:** numpy, a block at a time; no per-sample Python. The engine with 16
shaped pads ringing and the Abyss master uses under 10% of each 5.3 ms block. Pads trigger
from rtmidi's own thread, not a polling timer. The looper asks PipeWire for a 256-frame period
(`PIPEWIRE_LATENCY=256/48000`, this process only), so a pad sounds about 11 ms after you hit
it (the default 1024-frame graph would make that ~40 ms). Hits are recorded where you heard
them, with that latency taken off.

While Chimp Loop is open, the desktop service leaves the other controllers to it: no
desktop actions from the KO II's profile while you drum. The LPD8 stays with the desktop
while it's on a program with desktop actions (the looper says so in its title bar), so its
knobs don't turn the volume and the filter at once; switch program to drum.

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
which reports activations and fullscreen changes to `com.performativenonsense.chimp.service` on the session bus.
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
