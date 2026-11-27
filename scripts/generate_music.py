#!/usr/bin/env python3
"""
Generate 3 clean, royalty-free, seamless-loop ambient/lo-fi background tracks
in assets/music/ using pure Python synthesis + FFmpeg warm filtering.
"""

import math
import os
import struct
import subprocess
import tempfile
import wave

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC_DIR = os.path.join(REPO_ROOT, "assets", "music")
SR = 44100


def chord_freqs(midis):
    return [440.0 * (2.0 ** ((m - 69) / 12.0)) for m in midis]


def render_track(out_mp3, bpm, chords, bass_notes, pulse_hz=2.0):
    beat_sec = 60.0 / bpm
    bar_sec = beat_sec * 4.0
    dur = bar_sec * len(chords)
    n_samples = int(dur * SR)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name

    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SR)

            frames = bytearray()
            for i in range(n_samples):
                t = i / SR
                bar_idx = min(len(chords) - 1, int(t / bar_sec))
                bar_t = (t % bar_sec) / bar_sec

                # Smooth envelope at bar boundaries so chord transitions never click
                bar_env = min(1.0, bar_t / 0.08) * min(1.0, (1.0 - bar_t) / 0.08)
                bar_env = 0.35 + 0.65 * bar_env

                # Warm pad from fundamental + soft 2nd harmonic
                pad_l = 0.0
                pad_r = 0.0
                for k, f in enumerate(chord_freqs(chords[bar_idx])):
                    detune = 1.0 + (0.0022 if k % 2 == 0 else -0.0022)
                    s1 = math.sin(2.0 * math.pi * f * t)
                    s2 = math.sin(2.0 * math.pi * f * detune * t)
                    warm = 0.72 * s1 + 0.14 * math.sin(4.0 * math.pi * f * t)
                    warm_d = 0.72 * s2 + 0.14 * math.sin(4.0 * math.pi * f * detune * t)
                    pan = 0.35 + 0.3 * (k / max(1, len(chords[bar_idx]) - 1))
                    pad_l += warm * (1.0 - pan * 0.4)
                    pad_r += warm_d * (0.6 + pan * 0.4)

                pad_l /= len(chords[bar_idx])
                pad_r /= len(chords[bar_idx])

                # Soft sub bass
                bf = 440.0 * (2.0 ** ((bass_notes[bar_idx] - 69) / 12.0))
                bass = 0.32 * math.sin(2.0 * math.pi * bf * t)

                # Subtle rhythmic pulse (sidechain-like gentle swell)
                beat_phase = (t * pulse_hz) % 1.0
                pump = 0.72 + 0.28 * math.sin(math.pi * beat_phase)

                # Gentle soft tick on quarter notes
                q_phase = (t / beat_sec) % 1.0
                tick = 0.0
                if q_phase < 0.06:
                    tick_env = math.exp(-q_phase * 55.0)
                    tick = 0.06 * tick_env * math.sin(2.0 * math.pi * 160.0 * t * math.exp(-q_phase * 8.0))

                # Loop crossfade at start/end 0.15s
                loop_env = min(1.0, t / 0.15) * min(1.0, (dur - t) / 0.15)

                l_val = (pad_l * bar_env * pump + bass + tick) * 0.55 * loop_env
                r_val = (pad_r * bar_env * pump + bass + tick) * 0.55 * loop_env

                l_int = max(-32767, min(32767, int(l_val * 32767)))
                r_int = max(-32767, min(32767, int(r_val * 32767)))
                frames.extend(struct.pack("<hh", l_int, r_int))

            wf.writeframes(bytes(frames))

        # Warm low-pass + subtle stereo echo via ffmpeg -> compact MP3 loop
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", wav_path,
                "-af", "lowpass=f=2400,aecho=0.8:0.7:45|70:0.18|0.12",
                "-c:a", "libmp3lame", "-b:a", "128k",
                out_mp3,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


def main():
    os.makedirs(MUSIC_DIR, exist_ok=True)
    tracks = [
        # Chill minor 7th progression (Am7 - Fmaj7 - Dm7 - Em7)
        ("chill_lofi.mp3", 84, [[57, 60, 64, 67], [53, 57, 60, 64], [50, 53, 57, 60], [52, 55, 59, 62]], [45, 41, 38, 40], 1.4),
        # Tech pulse (Cm7 - Abmaj7 - Fm7 - Gm7)
        ("tech_pulse.mp3", 104, [[60, 63, 67, 70], [56, 60, 63, 67], [53, 56, 60, 63], [55, 58, 62, 65]], [36, 44, 41, 43], 1.733),
        # Deep focus ambient (Em9 - Cmaj7 - Gmaj7 - D6)
        ("ambient_deep.mp3", 76, [[52, 55, 59, 66], [48, 52, 55, 59], [55, 59, 62, 66], [50, 54, 57, 59]], [40, 36, 43, 38], 1.267),
    ]
    for name, bpm, chords, bass, pulse in tracks:
        path = os.path.join(MUSIC_DIR, name)
        render_track(path, bpm, chords, bass, pulse)
        kb = os.path.getsize(path) / 1024.0
        print(f"Generated {path} ({kb:.1f} KB)")


if __name__ == "__main__":
    main()
