"""Interactive probe: can Chimp still hear the mic while it's muted?

Talk when asked. Prints the peak level with the mic live, then with it muted
(PipeWire source mute), then puts the mute back how it was.
If "muted" stays at 0.0000 while you talk, a muted source is silenced for every
listener and "talking while muted" needs a different way of muting.

Run with: just mic-probe
"""

import time

import pulsectl


def measure(pulse, src, seconds: float) -> float:
    end, peak = time.monotonic() + seconds, 0.0
    while time.monotonic() < end:
        peak = max(peak, pulse.get_peak_sample(src, 0.25))
    return peak


with pulsectl.Pulse("chimp-mic-probe") as pulse:
    src = pulse.get_source_by_name(pulse.server_info().default_source_name)
    was_muted = src.mute
    print(f"Mic: {src.description}")
    try:
        pulse.mute(src, False)
        input("Press Enter, then talk for 3 seconds (mic LIVE)... ")
        live = measure(pulse, src, 3)
        pulse.mute(src, True)
        input("Press Enter, then talk for 3 seconds again (mic MUTED)... ")
        muted = measure(pulse, src, 3)
    finally:
        pulse.mute(src, was_muted)
    print(f"\npeak while live:  {live:.4f}\npeak while muted: {muted:.4f}")
    print("(mute put back to", "muted)" if was_muted else "live)")
    if live < 0.01:
        print("Didn't hear you even when live: is the default mic the one you talked into?")
    elif muted < 0.005:
        print("Muted = silent for everyone, including Chimp.")
    else:
        print("Chimp can still hear you while muted.")
