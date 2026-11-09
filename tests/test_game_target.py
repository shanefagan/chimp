"""What the Game volume knob points at: bpfrag's game (menu included), Proton siblings."""

import os

import pytest

from chimp.desktop import system
from chimp.desktop.focus import FocusTracker


def test_bpfrag_game_in_menu_counts_and_idle_clears():
    t = FocusTracker()
    seen = []
    t.changed.connect(lambda: seen.append(t.game()))
    me = os.getpid()
    t._on_bpfrag_state("menu", {"from": "idle", "pid": me, "game": "deadlock"})
    assert t.game() == {"pid": me, "name": "deadlock", "source": "bpfrag"}
    t._on_bpfrag_state("match", {"from": "menu", "pid": me, "game": "deadlock"})
    assert len(seen) == 1  # same game: no change
    t._on_bpfrag_state("idle", {"from": "match", "pid": me, "game": "deadlock"})
    assert t.game() is None and len(seen) == 2


def test_proton_sibling_stream_matched_by_steam_app_id(monkeypatch):
    streams = [
        {"id": 1, "name": "Firefox", "binary": "firefox", "node": "", "pid": 200, "running": True},
        {"id": 2, "name": "wine64-preloader", "binary": "wine64-preloader", "node": "", "pid": 301, "running": True},
    ]
    monkeypatch.setattr(system, "audio_streams", lambda max_age=0.5: streams)
    monkeypatch.setattr(system, "_ancestors", lambda pid, depth=12: [1])  # siblings: no shared parent chain
    monkeypatch.setattr(system, "proc_name", lambda pid: "deadlock.exe")
    ids = {300: "1422450", 301: "1422450", 200: ""}
    monkeypatch.setattr(system, "steam_app_id", lambda pid: ids.get(pid, ""))
    assert [s["id"] for s in system.streams_for_pid(300)] == [2]


def test_steam_app_id_reads_environ():
    assert system.steam_app_id(os.getpid()) == (os.environ.get("SteamAppId") or "")  # noqa: SIM112 - Steam's own spelling


def test_window_class_names_the_steam_game(monkeypatch):
    # the window's process (a wrapper) has no SteamAppId; the audio's sibling does
    streams = [
        {"id": 10, "name": "wine64-preloader", "binary": "wine64-preloader", "node": "", "pid": 501, "running": True},
    ]
    monkeypatch.setattr(system, "audio_streams", lambda max_age=0.5: streams)
    monkeypatch.setattr(system, "_ancestors", lambda pid, depth=12: [])
    monkeypatch.setattr(system, "proc_name", lambda pid: "TapeToTape.exe")
    ids = {500: "", 501: "1566200"}
    monkeypatch.setattr(system, "steam_app_id", lambda pid: ids.get(pid, ""))
    assert system.streams_for_pid(500) == []
    assert [s["id"] for s in system.streams_for_pid(500, app_class="steam_app_1566200")] == [10]


def test_class_app_id():
    assert system.class_app_id("steam_app_1566200") == "1566200"
    assert system.class_app_id("Steam_App_42 ") == "42"
    assert system.class_app_id("steam_app_") == ""
    assert system.class_app_id("not_steam_app_1") == ""
    assert system.class_app_id("firefox") == ""


def test_game_cache_notices_a_reused_pid(monkeypatch):
    me = os.getpid()  # pytest: not a game
    monkeypatch.setattr(system, "_game_cache", {})
    if system.looks_like_game(me):
        pytest.skip("this test run looks like a game (launched from Steam?)")
    system._game_cache[me] = (system._start_time(me), True)
    assert system.looks_like_game(me)  # same process: the cached answer
    system._game_cache[me] = ("1", True)  # a process that had this pid before
    assert not system.looks_like_game(me)


def test_audio_streams_reads_client_pid_for_native_pipewire(monkeypatch):
    """Proton's native PipeWire backend puts process.id and process.binary on the Client,
    not on the Stream/Output/Audio Node (e.g. Tape to Tape.exe)."""
    import json

    pw_objects = [
        {
            "id": 269,
            "type": "PipeWire:Interface:Client",
            "info": {
                "props": {
                    "application.name": "Tape to Tape.exe",
                    "application.process.binary": "wine64-preloader",
                    "application.process.id": 16133,
                    "pipewire.sec.pid": 16133,
                }
            },
        },
        {
            "id": 267,
            "type": "PipeWire:Interface:Node",
            "info": {
                "state": "running",
                "props": {
                    "application.name": "Tape to Tape.exe",
                    "client.id": 269,
                    "media.class": "Stream/Output/Audio",
                    "node.name": "Tape to Tape.exe",
                },
            },
        },
    ]
    monkeypatch.setattr(system, "_streams_cache", (0.0, []))
    monkeypatch.setattr(system, "run", lambda argv, timeout=2.0: json.dumps(pw_objects))
    streams = system.audio_streams(max_age=0)
    assert streams == [
        {
            "id": 267,
            "name": "Tape to Tape.exe",
            "binary": "wine64-preloader",
            "node": "Tape to Tape.exe",
            "pid": 16133,
            "running": True,
        }
    ]
    targets = {"game": {"pid": 16133, "name": "steam_app_1566200", "source": "fullscreen"}}
    resolved, label = system.resolve_streams("game_volume", "", targets)
    assert [s["id"] for s in resolved] == [267]
    assert label == "Tape to Tape"
