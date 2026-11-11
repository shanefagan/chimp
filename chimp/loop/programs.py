"""What each LPD8 mk2 program's pads do in Chimp Loop.

Out of the box all four programs send the same notes, so the program itself is the
context: the first one Chimp Loop gets is page A of the kit, the next page B, then the
FX page (roll and the glitches). Programs with desktop actions stay the desktop's.
Kept apart from the rest of the looper (no audio imports) so the editor can say which
program does what.
"""

from __future__ import annotations

import json

from .. import protocol
from ..desktop.config import CONFIG_DIR

FX = "fx"
# Jobs for the programs Chimp Loop gets, in order (those used for the desktop are skipped).
JOBS = ["A", "B", FX, "A"]


def lpd8_jobs(desktop: list[int] | set[int], jobs: list | None = None) -> dict[int, str]:
    """program -> "A", "B"... (a page of sounds) or "fx", for the programs that aren't
    the desktop's. `jobs` is your own order (loop.json's lpd8_jobs), if you set one."""
    mine = [j for j in (jobs or []) if j == FX or (isinstance(j, str) and len(j) == 1 and "A" <= j <= "P")]
    order = mine or JOBS
    free = [slot for slot in range(1, protocol.NUM_PROGRAMS + 1) if slot not in desktop]
    return {slot: order[i % len(order)] for i, slot in enumerate(free)}


def job_page(job: str) -> int:
    """The page of sounds a job letter stands for (A = 0); -1 for the FX page."""
    return -1 if job == FX else ord(job) - ord("A")


def saved_jobs() -> list:
    """Your own order from Chimp Loop's settings ([] = the usual one)."""
    try:
        jobs = json.loads((CONFIG_DIR / "loop.json").read_text()).get("lpd8_jobs")
    except (OSError, ValueError, AttributeError):
        return []
    return jobs if isinstance(jobs, list) else []


def describe(job: str) -> str:
    return "Loop FX" if job == FX else f"Loop {job}"
