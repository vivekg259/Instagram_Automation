#!/usr/bin/env python3
"""
Generate royalty-free studio transition SFX (whoosh, click, impact, riser)
in assets/sfx/ using Python synthesis + FFmpeg filtering.
"""

import math
import os
import random
import struct
import subprocess
import tempfile
import wave

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX_DIR = os.path.join(REPO_ROOT, "assets", "sfx")
SR = 44100


def _save_wav_and_mp3(samples, out_mp3, af_filter):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name
    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            buf = bytearray()
            for s in samples:
                val = max(-32767, min(32767, int(s * 32767)))
                buf.extend(struct.pack("<h", val))
            wf.writeframes(bytes(buf))
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-af", af_filter, "-c:a", "libmp3lame", "-b:a", "128k", out_mp3],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


def make_whoosh(out_mp3):
    rng = random.Random(7)
    dur = 0.38
    n = int(dur * SR)
    samples = []
    lp = 0.0
    for i in range(n):
        t = i / dur
        s_wave = max(0.0, math.sin(math.pi * t))
        env = s_wave ** 1.6
        # Sweep low-pass cutoff up then down
        alpha = 0.03 + 0.35 * (s_wave ** 2)
        noise = rng.uniform(-1.0, 1.0)
        lp = lp + alpha * (noise - lp)
        samples.append(lp * env * 0.75)
    _save_wav_and_mp3(samples, out_mp3, "highpass=f=180,lowpass=f=4200")


def make_click(out_mp3):
    dur = 0.075
    n = int(dur * SR)
    samples = []
    for i in range(n):
        t = i / SR
        env = math.exp(-t * 85.0)
        tone = 0.65 * math.sin(2.0 * math.pi * (1400.0 * math.exp(-t * 28.0)) * t)
        pop = 0.35 * math.sin(2.0 * math.pi * 420.0 * t)
        samples.append((tone + pop) * env * 0.7)
    _save_wav_and_mp3(samples, out_mp3, "highpass=f=150")


def make_impact(out_mp3):
    dur = 0.55
    n = int(dur * SR)
    samples = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        freq = 92.0 * math.exp(-t * 3.8) + 34.0
        phase += 2.0 * math.pi * freq / SR
        env = math.exp(-t * 6.5) * min(1.0, t / 0.008)
        sub = math.sin(phase) + 0.25 * math.sin(phase * 2.0) * math.exp(-t * 14.0)
        samples.append(sub * env * 0.85)
    _save_wav_and_mp3(samples, out_mp3, "lowpass=f=220")


def make_riser(out_mp3):
    dur = 0.75
    n = int(dur * SR)
    samples = []
    phase = 0.0
    for i in range(n):
        t = i / dur
        freq = 180.0 + 360.0 * (t ** 2)
        phase += 2.0 * math.pi * freq / SR
        env = (t ** 1.8) * min(1.0, (1.0 - t) / 0.08)
        samples.append(math.sin(phase) * env * 0.45)
    _save_wav_and_mp3(samples, out_mp3, "lowpass=f=1800")


def main():
    os.makedirs(SFX_DIR, exist_ok=True)
    make_whoosh(os.path.join(SFX_DIR, "whoosh.mp3"))
    make_click(os.path.join(SFX_DIR, "click.mp3"))
    make_impact(os.path.join(SFX_DIR, "impact.mp3"))
    make_riser(os.path.join(SFX_DIR, "riser.mp3"))
    for f in sorted(os.listdir(SFX_DIR)):
        p = os.path.join(SFX_DIR, f)
        print(f"SFX: {f} ({os.path.getsize(p)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
