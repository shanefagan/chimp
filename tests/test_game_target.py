"""What the Game volume knob points at: bpfrag's game (menu included), Proton siblings."""

import os

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


def test_steam_app_id_matches_window_class(monkeypatch):
    streams = [
        {"id": 10, "name": "wine64-preloader", "binary": "wine64-preloader", "node": "", "pid": 501, "running": True},
    ]
    monkeypatch.setattr(system, "audio_streams", lambda max_age=0.5: streams)
    monkeypatch.setattr(system, "_ancestors", lambda pid, depth=12: [])
    monkeypatch.setattr(system, "proc_name", lambda pid: "TapeToTape.exe")
    # Even if reading /proc/500/environ yields nothing (pid 500 is a launcher/window wrapper)
    ids = {500: "", 501: "1566200"}
    monkeypatch.setattr(system, "steam_app_id", lambda pid, app_class="": "1566200" if "1566200" in app_class else ids.get(pid, ""))
    # streams_for_pid using app_class should match stream 10
    assert [s["id"] for s in system.streams_for_pid(500, app_class="steam_app_1566200")] == [10]

