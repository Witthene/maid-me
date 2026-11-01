"""Tiny cyberpunk sound effects, synthesized on first use (no audio files shipped).

Played apart from the voice (winsound / paplay) so they never cut it. Volume is baked into the WAVs; changing it
just re-renders them.
"""
import math
import os
import random
import struct
import wave

from assistente import plat

RATE = 22050
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sfx")
volume = 0.25  # 0..1, set by the app
enabled = True
_made = None


def _env(i, n, attack=0.005, release=0.06):
    t, end = i / RATE, n / RATE
    return min(1.0, t / attack) * min(1.0, (end - t) / release)


def _chirp(dur, f0, f1, wave_kind="sine"):
    n = int(dur * RATE)
    out, ph = [], 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * i / n
        ph += 2 * math.pi * f / RATE
        s = math.sin(ph) if wave_kind == "sine" else (1.0 if math.sin(ph) > 0 else -1.0) * 0.5
        out.append(s * _env(i, n))
    return out


def _noise(dur, crush=6):
    n = int(dur * RATE)
    out, held = [], 0.0
    for i in range(n):
        if i % crush == 0:
            held = random.uniform(-1, 1)
        out.append(held * _env(i, n, release=0.03))
    return out


def _mix(*parts):
    """parts: (start_seconds, samples, gain)"""
    n = max(int(s * RATE) + len(x) for s, x, _ in parts)
    out = [0.0] * n
    for s, x, g in parts:
        o = int(s * RATE)
        for i, v in enumerate(x):
            out[o + i] += v * g
    return out


def _hack():
    """Cyberpunk-style breach: data chatter + rising hum, then a distorted sub drop when the mark lands."""
    # chatter: fast gated square blips on a minor scale, like quickhack data packets
    chatter = []
    scale = (440, 523, 587, 659, 784, 880, 1046, 1318)
    while len(chatter) < int(0.85 * RATE):
        f = random.choice(scale) * random.choice((1, 2))
        chatter += [x * 0.6 for x in _chirp(random.uniform(0.018, 0.04), f, f, "square")]
        chatter += [0.0] * random.randint(40, 400)
    # riser: detuned buzz climbing with a quickening tremolo
    n = int(0.9 * RATE)
    riser, p1, p2 = [], 0.0, 0.0
    for i in range(n):
        t = i / n
        f = 70 + 260 * t ** 2
        p1 += 2 * math.pi * f / RATE
        p2 += 2 * math.pi * f * 1.012 / RATE
        saw = ((p1 / math.pi) % 2 - 1) + ((p2 / math.pi) % 2 - 1)
        trem = 0.6 + 0.4 * math.sin(2 * math.pi * (6 + 30 * t) * i / RATE)
        riser.append(math.tanh(saw * 1.5) * trem * t * 0.5 * _env(i, n, 0.05, 0.02))
    # impact: distorted sub drop with a noisy crack on top
    n = int(0.7 * RATE)
    hit, ph = [], 0.0
    for i in range(n):
        t = i / RATE
        ph += 2 * math.pi * (38 + 90 * math.exp(-t * 18)) / RATE
        hit.append(math.tanh(math.sin(ph) * 3) * math.exp(-t * 4.5) * _env(i, n, 0.002, 0.1))
    crack = [x * math.exp(-i / RATE * 30) for i, x in enumerate(_noise(0.15, crush=3))]
    # stutter: the start of the hit repeated, like a corrupted buffer
    chunk = hit[:int(0.035 * RATE)]
    stutter = []
    for k in range(4):
        stutter += [x * (0.7 - k * 0.15) for x in chunk] + [0.0] * int(0.02 * RATE)
    return _mix((0, chatter, 0.25), (0, riser, 0.35), (0.9, hit, 0.9), (0.9, crack, 0.35),
                (1.3, stutter, 0.4), (1.05, _chirp(0.1, 1760, 1760), 0.12), (1.16, _chirp(0.16, 2093, 2093), 0.1))


def _soft():
    """Light tag: a few data blips, a short buzz rise and a small sub thump."""
    chatter = []
    for f in (880, 1046, 1318, 1046):
        chatter += [x * 0.6 for x in _chirp(0.022, f, f, "square")] + [0.0] * random.randint(150, 450)
    n = int(0.25 * RATE)
    rise, ph = [], 0.0
    for i in range(n):
        t = i / n
        ph += 2 * math.pi * (110 + 180 * t) / RATE
        rise.append(math.tanh(((ph / math.pi) % 2 - 1) * 1.5) * t * 0.4 * _env(i, n, 0.03, 0.02))
    n = int(0.3 * RATE)
    hit, ph = [], 0.0
    for i in range(n):
        t = i / RATE
        ph += 2 * math.pi * (45 + 70 * math.exp(-t * 22)) / RATE
        hit.append(math.tanh(math.sin(ph) * 2) * math.exp(-t * 9) * _env(i, n, 0.002, 0.06))
    chunk = hit[:int(0.025 * RATE)]
    stutter = [x * 0.5 for x in chunk] + [0.0] * int(0.015 * RATE) + [x * 0.3 for x in chunk]
    return _mix((0, chatter, 0.18), (0, rise, 0.25), (0.25, hit, 0.6),
                (0.42, stutter, 0.3), (0.3, _chirp(0.08, 1760, 1760), 0.08))


def _sounds():
    random.seed(7)  # same sounds every time
    glitch = []
    for _ in range(7):  # stuttering data bursts
        glitch += _noise(random.uniform(0.012, 0.035), crush=random.choice((3, 5, 9))) + [0.0] * random.randint(60, 500)
    return {
        "open": _mix((0, _chirp(0.07, 900, 1800), 0.5), (0.08, _chirp(0.09, 1800, 2400), 0.35)),
        "hack": _hack(),
        "alarm": _mix((0, _chirp(0.14, 620, 620, "square"), 0.3), (0.2, _chirp(0.14, 620, 620, "square"), 0.3),
                      (0.4, _chirp(0.22, 460, 460, "square"), 0.3)),
        "soft": _soft(),
        "click": _mix((0, _chirp(0.03, 2600, 1900), 0.4), (0, _noise(0.012, 2), 0.15)),
        "glitch": _mix((0, glitch[:int(RATE * 0.18)], 0.4)),
        # CRT power-off: falling chirp and a tiny static pop
        "close": _mix((0, _chirp(0.12, 2200, 500), 0.4), (0.1, _noise(0.04, 4), 0.2)),
        "purge": _purge(),
        # radar: two sonar pings with a soft echo
        "search": _mix((0, _chirp(0.12, 1400, 1300), 0.35), (0.18, _chirp(0.12, 1400, 1300), 0.12),
                       (0.6, _chirp(0.12, 1700, 1600), 0.3), (0.78, _chirp(0.12, 1700, 1600), 0.1)),
        "download": _mix(*[(i * 0.07, _chirp(0.03, 1800 - i * 90, 1800 - i * 90, "square"), 0.15) for i in range(14)],
                         (1.0, _chirp(0.08, 1046, 1046), 0.3), (1.08, _chirp(0.14, 1568, 1568), 0.3)),
        "error": _mix((0, _chirp(0.18, 220, 210, "square"), 0.35), (0.22, _chirp(0.3, 165, 150, "square"), 0.35),
                      (0, glitch[:int(RATE * 0.18)], 0.3)),
        # camera: focus beeps, then a mechanical shutter click-clack
        "snap": _mix((0, _chirp(0.05, 1800, 1800), 0.2), (0.12, _chirp(0.05, 2400, 2400), 0.2),
                     (0.4, _noise(0.03, 2), 0.6), (0.47, _noise(0.05, 4), 0.45), (0.4, _chirp(0.06, 800, 200), 0.3)),
        "kill": _mix((0, _soft(), 0.8), (0.65, _chirp(0.35, 2400, 300), 0.35), (0.95, _noise(0.05, 4), 0.25)),
        "launch": _mix((0, _chirp(0.25, 180, 1400, "square"), 0.25), (0.05, _chirp(0.3, 300, 2600), 0.3),
                       (0.3, _chirp(0.08, 2093, 2093), 0.12)),
    }


def _purge():
    """Data shredder: crunchy noise bursts falling in pitch, ending in a low thud."""
    out = []
    for k in range(10):
        out += _noise(0.03, crush=3 + k) + [0.0] * int(0.025 * RATE)
    n = int(0.4 * RATE)
    thud, ph = [], 0.0
    for i in range(n):
        t = i / RATE
        ph += 2 * math.pi * (40 + 60 * math.exp(-t * 15)) / RATE
        thud.append(math.tanh(math.sin(ph) * 2.5) * math.exp(-t * 7) * _env(i, n, 0.002, 0.08))
    return _mix((0, out, 0.3), (0, _chirp(0.55, 900, 120, "square"), 0.12), (0.55, thud, 0.8))


def _render():
    global _made
    os.makedirs(DIR, exist_ok=True)
    for name, samples in _sounds().items():
        peak = max(1e-6, max(abs(s) for s in samples))
        gain = 32767 * volume / peak
        with wave.open(os.path.join(DIR, name + ".wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<h", int(s * gain)) for s in samples))
    _made = volume


def set_volume(v):
    global volume
    volume = max(0.0, min(1.0, v))


def play(name):
    if not enabled or volume <= 0:
        return
    try:
        if _made != volume:
            _render()
        plat.play_wav(os.path.join(DIR, name + ".wav"))
    except Exception:
        pass
