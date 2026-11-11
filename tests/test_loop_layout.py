"""Chimp Loop shaped like the controller in your hands: pages of pads, what each pad does."""

from chimp import controllers, protocol
from chimp.loop import layout
from chimp.loop.mapping import ACTIONS, HOLD_ACTIONS, LoopSettings


def test_the_pads_take_the_controllers_shape():
    lpd8 = layout.shape_of("LPD8 mk2")
    assert (lpd8.columns, lpd8.rows, lpd8.size, lpd8.knobs) == (4, 2, 8, 8)
    assert layout.page_count(lpd8) == 2
    assert layout.page_sounds(lpd8, 1) == list(range(8, 16))
    # drawn like the hardware: pad 1 bottom-left, 5-8 above 1-4
    assert layout.cells(lpd8, 8) == [4, 5, 6, 7, 0, 1, 2, 3]
    ko2 = layout.shape_of("EP-133")
    assert (ko2.columns, ko2.rows, layout.page_count(ko2)) == (3, 4, 2)
    assert layout.page_sounds(ko2, 1) == [12, 13, 14, 15]  # the kit runs out: the rest are gaps
    assert layout.cells(ko2, 4) == [0, 1, 2, 3] + [-1] * 8  # pad 1 top-left on this one
    assert layout.shape_of("APC MINI").size == 16  # more pads than sounds: the kit, 4 x 4
    assert layout.shape_of("nanoKONTROL2") is None and layout.shape_of("Some Synth") is None
    assert layout.shape_of("MPD218").size == 16  # only the shape is known: enough here
    assert (layout.forced(12).columns, layout.forced(8).rows, layout.forced(16).size) == (3, 2, 16)


def test_lpd8_programs_get_jobs_and_the_desktops_are_left_out():
    assert layout.lpd8_jobs({4}) == {1: "A", 2: "B", 3: "fx"}
    assert layout.lpd8_jobs(set()) == {1: "A", 2: "B", 3: "fx", 4: "A"}
    assert layout.lpd8_jobs({1, 4}) == {2: "A", 3: "B"}
    assert layout.lpd8_jobs({4}, ["B", "nonsense", 7, "A"]) == {1: "B", 2: "A", 3: "B"}  # yours, tidied
    assert set(layout.FX_PADS) <= HOLD_ACTIONS and set(layout.FX_PADS) <= set(ACTIONS)
    assert len(layout.FX_PADS) == 8  # one each for an LPD8's pads


def test_lpd8_pads_map_by_position_whatever_they_send():
    prog = protocol.Program(global_channel=3)  # out of the box every program sends notes 36-43
    for i, pad in enumerate(prog.pads):
        pad.note, pad.cc, pad.channel = 36 + i, 20 + i, 10
    prog.pads[7].channel = protocol.GLOBAL_CHANNEL
    prog.knobs[0].cc, prog.knobs[0].channel = 20, 10  # a knob sharing pad 1's CC
    a, b, fx = (layout.lpd8_table(prog, job) for job in ("A", "B", "fx"))
    assert (a["note:10:36"], b["note:10:36"], fx["note:10:36"]) == ("sound:0", "sound:8", "roll")
    assert (a["note:3:43"], b["note:3:43"]) == ("sound:7", "sound:15")  # the pad on the global channel
    assert b["cc:10:21"] == "sound:9"  # pads in CC mode
    assert a["cc:10:20"] == "knob:0"  # the knob wins the shared CC
    assert a["cc:3:71"] == "knob:1"
    assert fx["note:10:37"] == "g.stutter4" and fx["note:3:43"] == "g.half"


def test_a_known_controllers_pads_and_knobs():
    ko2 = layout.table_for(controllers.find("EP-133"))
    assert (ko2["note:1:36"], ko2["note:1:47"]) == ("sound:0", "sound:11")
    assert (ko2["note:1:48"], ko2["note:1:51"], ko2["note:1:52"]) == ("sound:12", "sound:15", "")  # group B
    assert ko2["note:1:60"] == "sound:0"  # group C starts the kit again
    apc = layout.table_for(controllers.find("APC MINI"))
    assert (apc["note:1:0"], apc["note:1:15"], apc["note:1:16"]) == ("sound:0", "sound:15", "sound:0")
    assert apc["cc:1:48"] == "knob:0" and apc["note:1:98"] == ""  # faders as knobs; Shift does nothing
    mix = layout.table_for(controllers.find("MIDI Mix"))
    assert mix["cc:1:16"] == "knob:0" and mix["cc:1:19"] == ""  # eight knobs; the rest wait to be assigned
    assert mix["note:1:1"] == ""  # a mute button isn't a drum


def test_what_chimp_knows_sits_between_your_assignments_and_the_defaults():
    s = LoopSettings()
    table = {"note:10:36": "sound:8", "note:1:1": "", "cc:1:16": "knob:0"}
    assert s.target("X", "note:10:36") == "sound:0"  # any controller: note 36 is the first sound
    assert s.target("X", "note:10:36", table) == "sound:8"
    assert s.target("X", "note:1:1", table) is None  # known, and nothing on it
    assert s.target("X", "note:10:40", table) == "sound:4"  # not in the table: the defaults
    s.set_assign("X", "note:10:36", "action:record")
    s.set_assign("X", "note:1:1", "sound:3")
    assert s.target("X", "note:10:36", table) == "record" and s.target("X", "note:1:1", table) == "sound:3"


def test_knob_pages_reach_every_dial_once():
    from chimp.loop import voice
    from chimp.loop.engine import KNOB_PAGE_LABELS, KNOB_PAGES, MASTER_KNOBS, Engine

    dials = [f"p.{k}" for k in voice.PAD_KNOBS] + [f"m.{k}" for k in MASTER_KNOBS]
    on_pages = [pid for page in KNOB_PAGES.values() for pid in page]
    assert sorted(on_pages) == sorted(dials)
    assert all(len(page) <= 8 for page in KNOB_PAGES.values()) and set(KNOB_PAGE_LABELS) == set(KNOB_PAGES)
    e = Engine(48000, background=False)
    e.knob_page = "pad2"
    assert e.knob_target(0) == "p.pan" and e.knob_target(7) is None  # a short page: the last knobs rest
