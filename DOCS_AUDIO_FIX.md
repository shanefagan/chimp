# Chimp Audio & Game Target Investigation & Fixes

**Date:** 2026-11-08  
**Repository:** `/home/shane/Projects/lpd8mk2-editor`  
**Branch:** `fix-game-volume`

---

## 1. Issue Observed

In non-competitive / single-player games (specifically tested earlier with *Tape to Tape*, Steam App ID `1566200`, window PID `585171`), turning Knob 8 (assigned to `game_volume` on Program 4) showed the game title and the percentage level decreasing in the on-screen overlay / OSD, but the actual game sound did not decrease.

Checking the journal logs (`journalctl --user -u chimp`) revealed:
```text
Nov 08 10:12:00 cachyos-x8664 chimpd[4358]: game: steam_app_1566200 (pid 585171, via fullscreen)
Nov 08 10:13:43 cachyos-x8664 chimpd[4358]: Knob 8 → steam_app_1566200 (no sound): 57%
Nov 08 10:13:43 cachyos-x8664 chimpd[4358]: Knob 8 → steam_app_1566200 (no sound): 43%
...
Nov 08 10:13:51 cachyos-x8664 chimpd[4358]: Knob 8 → steam_app_1566200 (no sound): 50%
```

Notice the suffix **`(no sound)`**.

---

## 2. Root Cause Analysis

### A. How competitive vs. non-competitive games are tracked
* **Competitive games** (e.g. *Deadlock*): `bpfrag` runs on the D-Bus system bus. It tracks active game network traffic and emits `StateChanged` with the game name and exact game PID. `streams_for_pid` then easily finds the audio stream or matching Steam app ID.
* **Non-competitive games**: Detected purely via fullscreen window events through a KWin script (`FocusTracker._on_window`). KWin reports the active fullscreen window PID and its X11/Wayland resource class (e.g., `app = "steam_app_1566200"`, `pid = 585171`).

### B. Why `streams_for_pid` failed to find the audio stream
Under Steam / Proton on Linux:
1. The process owning the fullscreen X11/Wayland window is often a wrapper/launcher process or sibling thread (e.g., `pid = 585171`).
2. The actual audio playback stream in PipeWire runs under a sibling process (`wine64-preloader` or similar), not a direct child or parent.
3. `streams_for_pid(pid)` tried to link sibling processes using `steam_app_id(pid)` by reading `/proc/{pid}/environ` looking for `SteamAppId=...`.
4. However, reading `/proc/{pid}/environ` can fail or return empty if the wrapper process lacks the environment variable, or if security/namespace boundaries prevent reading it.
5. Meanwhile, KWin had **already provided the exact Steam App ID** in the window resource class name (`steam_app_1566200`)! But `resolve_streams` called `streams_for_pid(int(t["pid"]))` without passing `t["name"]`, throwing away the already-known App ID.
6. The fallback in `streams_for_pid` compares `proc_name(pid)` (e.g., `TapeToTape.exe`) with `s["binary"]` (`wine64-preloader`). Because neither starts with the other, it failed and returned an empty list `[]`.

### C. Why the UI showed the percentage despite no audio changing
In `chimp/desktop/actions.py`:
* In `_apply_level()`, when `a.type in system.STREAM_ACTIONS`:
  ```python
  streams, label = system.resolve_streams(a.type, a.target, self._targets())
  for s in streams:
      system.set_volume(s["id"], frac)
  ...
  self._done(who, label, pct)
  ```
* Even when `streams` was empty (`[]`), `_done(who, label, pct)` passed the knob's raw position (`pct = 50%`) to the feedback callback.
* `OverlayModel.value()` saved `(label, pct)` in `_values`, causing `Overlay.qml` to render `K8 50%` with a filled 50% progress bar, falsely signaling to the user that the volume had been changed.

### D. Process cache poisoning in `looks_like_game`
In `chimp/desktop/system.py`:
* `_game_cache: dict[int, bool]` cached PIDs forever without checking if the process had died or if the PID was later recycled by Linux for another process.

---

## 3. How It Was Fixed

### Fix 1: Pass window class / name into `steam_app_id` and `streams_for_pid`
* Updated `steam_app_id(pid: int, app_class: str = "") -> str` in `chimp/desktop/system.py`:
  If `app_class` contains `steam_app_(\d+)`, it immediately extracts and uses the Steam App ID.
* Updated `streams_for_pid(pid: int, app_class: str = "")`:
  Resolves Proton sibling audio streams using `steam_app_id(pid, app_class)`.
* Updated `resolve_streams()`:
  Passes `app_class=str(t.get("name") or "")` to `streams_for_pid()`.

### Fix 2: Truthful feedback when no streams exist
* Updated `ActionRunner._apply_level()` and `_apply_step()` in `chimp/desktop/actions.py`:
  When `streams` is empty, passes `pct=None` to `_done()`.
* In `Overlay.qml`, when `pct` is `None` (level < 0), the percentage text is blank/dash and the progress bar is hidden, accurately reflecting that no active stream was adjusted.

### Fix 3: Cache validation in `looks_like_game`
* Updated `looks_like_game()` in `chimp/desktop/system.py` to only return cached results if `pid_alive(pid)` is true.

---

## 4. Verification

* Added unit test `test_steam_app_id_matches_window_class` in `tests/test_game_target.py` verifying that wrapper PIDs with empty environ still match their sibling audio stream via `app_class="steam_app_1566200"`.
* Ran complete test suite:
  ```bash
  PYTHONPATH=. pytest tests
  ```
  Result: **108 passed in 4.80s**.
* Ran linter:
  ```bash
  .venv/bin/ruff check chimp tests
  ```
  Result: **All checks passed!**
