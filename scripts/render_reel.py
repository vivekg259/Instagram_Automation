#!/usr/bin/env python3
"""
Video Engine v2: 1080x1920 30fps faceless Instagram Reel renderer.

Features ($0, works on Termux/Android and Ubuntu GitHub Actions):
  1. AI Voiceover via edge-tts (neural voices) with exact beat-level sync
  2. Word-by-word karaoke-style animated captions from WordBoundary events
  3. Dynamic per-beat B-roll / mockup visuals (terminal, comparison, ui_steps,
     metric gauge, or Ken-Burns image/broll) inside IG safe zone (x=90..835,
     y=240..1430)
  4. Royalty-free background music mixed underneath voiceover via FFmpeg
"""

import argparse
import asyncio
import json
import math
import os
import random
import re
import struct
import subprocess
import tempfile
import urllib.request
from PIL import Image, ImageDraw, ImageFont

try:
    import edge_tts
except ImportError:
    edge_tts = None

WIDTH, HEIGHT = 1080, 1920
FPS = 30
SAFE_LEFT = 90
SAFE_RIGHT = 835
SAFE_TOP = 240
MAX_TEXT_W = SAFE_RIGHT - SAFE_LEFT
AUDIO_SR = 24000

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_FONTS = os.path.join(REPO_ROOT, "fonts")
MUSIC_DIR = os.path.join(REPO_ROOT, "assets", "music")

MOOD_PALETTES = {
    "hook": ((28, 12, 62), (98, 32, 138), (195, 115, 255)),
    "cool": ((14, 18, 48), (38, 46, 108), (110, 165, 255)),
    "dark": ((12, 12, 26), (28, 22, 54), (130, 115, 175)),
    "rise": ((18, 16, 58), (68, 36, 132), (165, 130, 255)),
    "flat": ((16, 18, 34), (34, 38, 66), (140, 155, 195)),
    "hero": ((32, 14, 72), (110, 44, 156), (225, 155, 255)),
    "loop": ((20, 12, 48), (65, 25, 105), (185, 115, 255)),
}


def load_font(candidates, size):
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


FONT_CARD = load_font([
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-Black.ttf"),
    "/system/fonts/BarlowSemiCondensed-Black.ttf",
    "/system/fonts/BarlowSemiCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/system/fonts/DroidSans-Bold.ttf",
], 78)

FONT_CAPTION = load_font([
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-SemiBold.ttf"),
    "/system/fonts/BarlowSemiCondensed-SemiBold.ttf",
    "/system/fonts/BarlowSemiCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/system/fonts/DroidSans-Bold.ttf",
], 44)

FONT_BADGE = load_font([
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-SemiBold.ttf"),
    "/system/fonts/BarlowSemiCondensed-SemiBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/system/fonts/DroidSans-Bold.ttf",
], 28)

FONT_MONO = load_font([
    "/system/fonts/DroidSansMono.ttf",
    "/system/fonts/RobotoMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-Medium.ttf"),
], 30)

FONT_UI = load_font([
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-Medium.ttf"),
    "/system/fonts/BarlowSemiCondensed-Medium.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/system/fonts/DroidSans.ttf",
], 34)

FONT_METRIC = load_font([
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-Black.ttf"),
    "/system/fonts/BarlowSemiCondensed-Black.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/system/fonts/DroidSans-Bold.ttf",
], 112)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def wrap_text(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if cur and (bbox[2] - bbox[0]) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def make_gradient_bg(top_col, bot_col):
    strip = Image.new("RGBA", (1, 64))
    px = strip.load()
    for y in range(64):
        px[0, y] = lerp_color(top_col, bot_col, y / 63.0) + (255,)
    return strip.resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)


def make_glow_sprite(size=560):
    sprite = Image.new("L", (size, size), 0)
    cx = size / 2.0
    px = sprite.load()
    for y in range(size):
        dy = (y - cx) / cx
        for x in range(size):
            dx = (x - cx) / cx
            d2 = dx * dx + dy * dy
            if d2 < 1.0:
                px[x, y] = int(255 * ((1.0 - d2) ** 2.2))
    return sprite


GLOW_MASK = make_glow_sprite(560)


def init_particles(count=55, seed=42):
    rng = random.Random(seed)
    return [
        (rng.uniform(0, WIDTH), rng.uniform(0, HEIGHT),
         rng.uniform(0.4, 1.5), rng.randint(2, 5), rng.uniform(0, 6.28))
        for _ in range(count)
    ]


# ---------------------------------------------------------------------------
# Voiceover Synthesis (edge-tts + WordBoundary timestamps)
# ---------------------------------------------------------------------------

async def _synth_clip(text, voice, rate, out_mp3):
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    boundaries = []
    with open(out_mp3, "wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                e = (chunk["offset"] + chunk["duration"]) / 1e7
                boundaries.append({"text": chunk["text"], "start": s, "end": e})
    return boundaries


def synthesize_voiceover(beats, voice, rate, tmp_dir):
    """Synthesize TTS per beat, sync beat durations, and map word timings."""
    if edge_tts is None:
        return None

    pcm_chunks = []
    clock = 0.0

    for idx, b in enumerate(beats):
        spoken = b.get("spoken") or b["caption"]
        mp3_path = os.path.join(tmp_dir, f"vo_{idx:02d}.mp3")
        raw_path = os.path.join(tmp_dir, f"vo_{idx:02d}.pcm")

        boundaries = []
        ok = False
        for attempt in range(3):
            try:
                boundaries = asyncio.run(_synth_clip(spoken, voice, rate, mp3_path))
                if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 200:
                    ok = True
                    break
            except Exception:
                pass

        if not ok:
            return None

        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-f", "s16le", "-ac", "1", "-ar", str(AUDIO_SR), raw_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pcm = open(raw_path, "rb").read()
        tts_dur = len(pcm) / (2 * AUDIO_SR)

        # Keep beat length tight to speech + crisp 0.16s breathing room
        dur = max(0.85, round(tts_dur + 0.16, 3))
        b["start"] = round(clock, 3)
        b["dur"] = dur

        # Map WordBoundary timestamps onto the display caption words smoothly
        disp_words = b["caption"].split()
        b["word_timings"] = align_words(disp_words, boundaries, tts_dur)

        # Pad PCM with exact trailing silence to match dur
        target_bytes = int(round(dur * AUDIO_SR)) * 2
        if len(pcm) < target_bytes:
            pcm += b"\x00" * (target_bytes - len(pcm))
        else:
            pcm = pcm[:target_bytes]
        pcm_chunks.append(pcm)
        clock += dur

    vo_wav = os.path.join(tmp_dir, "voiceover.wav")
    import wave
    with wave.open(vo_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(AUDIO_SR)
        wf.writeframes(b"".join(pcm_chunks))
    return vo_wav


def align_words(disp_words, boundaries, total_tts_dur):
    """Map spoken WordBoundary timings proportionally onto display words."""
    if not disp_words:
        return []
    n_d = len(disp_words)
    if not boundaries:
        step = total_tts_dur / n_d
        return [(i * step, (i + 1) * step) for i in range(n_d)]

    n_b = len(boundaries)
    if n_d == n_b:
        return [(b["start"], b["end"]) for b in boundaries]

    # Proportional mapping handles cases like "$20" -> "twenty dollars"
    timings = []
    for i in range(n_d):
        bi_start = min(n_b - 1, int(i * n_b / n_d))
        bi_end = min(n_b - 1, max(bi_start, int((i + 1) * n_b / n_d) - 1))
        timings.append((boundaries[bi_start]["start"], boundaries[bi_end]["end"]))
    return timings


# ---------------------------------------------------------------------------
# Dynamic Visual Stage Renderers (y=545..1005, x=90..835)
# ---------------------------------------------------------------------------

def infer_visual(beat):
    """Provide rich visual defaults when a spec omits explicit 'visual'."""
    if "visual" in beat and isinstance(beat["visual"], dict):
        return beat["visual"]
    card = beat["card"]
    cap = beat["caption"]
    mood = beat.get("mood", "cool")
    if any(k in cap.lower() for k in ("api", "curl", "endpoint", "token", "tpm", "413", "429", ":free", "model")):
        return {
            "type": "terminal",
            "title": "api-setup ~ zsh",
            "lines": [
                f"$ check --topic \"{card.lower()}\"",
                "-> status: 200 OK (free tier)",
                f"-> {cap[:38]}",
            ],
        }
    if any(k in cap.lower() for k in ("settings", "billing", "click", "open", "menu", "tab")):
        return {
            "type": "ui_steps",
            "title": "Account Setup",
            "steps": ["1. Open Settings", "2. Billing & Plans", "3. Save Info ($0)"],
            "active": 1 if "billing" in cap.lower() else 0,
        }
    m = re.search(r"(\$?\d[\d,.]*[kKmM%]?)", card + " " + cap)
    if m:
        return {
            "type": "metric",
            "value": m.group(1),
            "label": card,
        }
    if mood in ("dark", "flat"):
        return {
            "type": "comparison",
            "left_title": "PAID PLAN",
            "left_val": "$20/mo",
            "right_title": "FREE TIER",
            "right_val": "$0",
        }
    return {"type": "metric", "value": "FREE", "label": card}


def draw_terminal_visual(draw, box, vis, elapsed, dur, acc_col):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=22, fill=(10, 12, 24, 215), outline=acc_col + (140,), width=2)
    # Title bar
    draw.rounded_rectangle([x1, y1, x2, y1 + 52], radius=22, fill=(22, 25, 44, 230))
    draw.rectangle([x1, y1 + 30, x2, y1 + 52], fill=(22, 25, 44, 230))
    for i, dot_col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = x1 + 28 + i * 26
        cy = y1 + 26
        draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=dot_col + (235,))
    draw.text((x1 + 115, y1 + 12), vis.get("title", "terminal"), font=FONT_BADGE, fill=(180, 186, 215, 220))

    lines = vis.get("lines", ["$ run free-tier", "status: 200 OK"])
    all_text = "\n".join(lines)
    progress = min(1.0, elapsed / max(0.4, dur * 0.72))
    chars_to_show = max(1, int(len(all_text) * progress))
    shown = all_text[:chars_to_show]

    cur_y = y1 + 78
    for idx_l, ln in enumerate(shown.split("\n")):
        wrapped = wrap_text(draw, ln, FONT_MONO, (x2 - x1) - 48)
        for wl in wrapped:
            col = (120, 240, 170, 245) if wl.startswith("->") or "200 OK" in wl else (235, 238, 255, 245)
            if wl.startswith("$"):
                col = acc_col + (255,)
            draw.text((x1 + 24, cur_y), wl, font=FONT_MONO, fill=col)
            cur_y += 40
            if cur_y > y2 - 42:
                return
    # Blinking cursor
    if int(elapsed * 4) % 2 == 0 and cur_y <= y2 - 36:
        draw.rectangle([x1 + 24, cur_y, x1 + 40, cur_y + 28], fill=acc_col + (230,))


def draw_comparison_visual(draw, box, vis, elapsed, dur, acc_col):
    x1, y1, x2, y2 = box
    mid = (x1 + x2) // 2
    gap = 16
    pop = ease_out_cubic(elapsed / 0.35)

    # Left card (dim / old paid)
    draw.rounded_rectangle([x1, y1, mid - gap // 2, y2], radius=22,
                           fill=(18, 16, 34, 190), outline=(140, 130, 165, 95), width=2)
    draw.text((x1 + 28, y1 + 32), vis.get("left_title", "PAID"), font=FONT_BADGE, fill=(175, 175, 195, 210))
    draw.text((x1 + 28, y1 + 95), vis.get("left_val", "$20/mo"), font=FONT_CARD, fill=(195, 130, 145, 225))
    # Strike line
    if pop > 0.4:
        sx2 = int(lerp(x1 + 24, mid - gap // 2 - 24, min(1.0, (pop - 0.4) / 0.6)))
        draw.line([(x1 + 24, y1 + 142), (sx2, y1 + 142)], fill=(255, 100, 120, 230), width=6)

    # Right card (bright / free tier)
    rx1 = mid + gap // 2
    draw.rounded_rectangle([rx1, y1, x2, y2], radius=22,
                           fill=(26, 18, 56, 225), outline=acc_col + (220,), width=3)
    draw.text((rx1 + 28, y1 + 32), vis.get("right_title", "FREE TIER"), font=FONT_BADGE, fill=acc_col + (250,))
    draw.text((rx1 + 28, y1 + 95), vis.get("right_val", "$0"), font=FONT_CARD, fill=(255, 255, 255, 255))

    # Animated bottom bar inside right card
    bar_w = int(((x2 - rx1) - 56) * pop)
    draw.rectangle([rx1 + 28, y2 - 42, x2 - 28, y2 - 28], fill=(255, 255, 255, 45))
    if bar_w > 0:
        draw.rectangle([rx1 + 28, y2 - 42, rx1 + 28 + bar_w, y2 - 28], fill=(100, 245, 175, 240))


def draw_ui_steps_visual(draw, box, vis, elapsed, dur, acc_col):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=22, fill=(14, 16, 32, 215), outline=acc_col + (130,), width=2)
    draw.text((x1 + 28, y1 + 22), vis.get("title", "Quick Setup"), font=FONT_BADGE, fill=acc_col + (235,))

    steps = vis.get("steps", ["Step 1", "Step 2", "Step 3"])[:3]
    active = vis.get("active", min(len(steps) - 1, int(elapsed / max(0.35, dur / len(steps)))))
    row_h = 68
    cur_y = y1 + 72
    for i, st in enumerate(steps):
        is_act = (i == active)
        bg = (48, 30, 96, 235) if is_act else (24, 26, 48, 175)
        ol = acc_col + (235,) if is_act else (255, 255, 255, 35)
        draw.rounded_rectangle([x1 + 24, cur_y, x2 - 24, cur_y + row_h], radius=14, fill=bg, outline=ol, width=2)
        col = (255, 255, 255, 255) if is_act else (185, 190, 215, 200)
        draw.text((x1 + 46, cur_y + 16), st, font=FONT_UI, fill=col)
        if is_act:
            pulse = 12 + int(4 * math.sin(elapsed * 8.0))
            cx, cy = x2 - 60, cur_y + row_h // 2
            draw.ellipse([cx - pulse, cy - pulse, cx + pulse, cy + pulse], outline=(100, 245, 175, 235), width=3)
            draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(100, 245, 175, 255))
        cur_y += row_h + 14


def draw_metric_visual(draw, box, vis, elapsed, dur, acc_col):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=22, fill=(16, 14, 38, 195), outline=acc_col + (135,), width=2)
    pop = ease_out_cubic(elapsed / 0.3)
    val = str(vis.get("value", "FREE"))
    lbl = str(vis.get("label", ""))

    # Animated arc gauge on the right
    cx, cy, r = x2 - 125, (y1 + y2) // 2, 72
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=0, end=360, fill=(255, 255, 255, 35), width=12)
    sweep = int(285 * pop)
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=-90 + sweep, fill=acc_col + (245,), width=12)

    draw.text((x1 + 34, y1 + 48), val, font=FONT_METRIC, fill=(255, 255, 255, int(255 * pop)))
    draw.text((x1 + 36, y1 + 195), lbl[:32], font=FONT_BADGE, fill=acc_col + (235,))


def load_broll_image(src, tmp_dir):
    if not src:
        return None
    if os.path.exists(src):
        return Image.open(src).convert("RGBA")
    if src.startswith("http"):
        cache = os.path.join(tmp_dir, f"broll_{abs(hash(src))}.img")
        if not os.path.exists(cache):
            req = urllib.request.Request(src, headers={"User-Agent": "ig-reel-engine/2.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                open(cache, "wb").write(r.read())
        return Image.open(cache).convert("RGBA")
    return None


def draw_broll_visual(overlay, draw, box, img_obj, elapsed, dur, acc_col):
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    prog = min(1.0, elapsed / max(0.5, dur))
    zoom = 1.0 + 0.12 * prog
    iw, ih = img_obj.size
    scale = max(bw / iw, bh / ih) * zoom
    nw, nh = int(iw * scale), int(ih * scale)
    resized = img_obj.resize((nw, nh), Image.Resampling.BILINEAR)
    cx = max(0, (nw - bw) // 2 + int(12 * prog))
    cy = max(0, (nh - bh) // 2)
    crop = resized.crop((cx, cy, cx + bw, cy + bh))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw, bh], radius=22, fill=255)
    overlay.paste(crop, (x1, y1), mask)
    draw.rounded_rectangle([x1, y1, x2, y2], radius=22, outline=acc_col + (175,), width=3)


# ---------------------------------------------------------------------------
# Karaoke Caption Renderer
# ---------------------------------------------------------------------------

def draw_karaoke_captions(draw, beat, elapsed, pop, acc_col):
    words = beat["caption"].split()
    if not words:
        return
    timings = beat.get("word_timings")
    if not timings or len(timings) != len(words):
        step = beat["dur"] / len(words)
        timings = [(i * step, (i + 1) * step) for i in range(len(words))]

    # Find active word index
    active_idx = len(words) - 1
    for i, (s, e) in enumerate(timings):
        if elapsed < e:
            active_idx = i
            break

    # Wrap words into lines while preserving word index
    max_w = MAX_TEXT_W - 44
    lines = []
    cur_line = []
    cur_w = 0
    space_w = draw.textbbox((0, 0), " ", font=FONT_CAPTION)[2]
    for idx_w, w in enumerate(words):
        ww = draw.textbbox((0, 0), w, font=FONT_CAPTION)[2]
        if cur_line and cur_w + space_w + ww > max_w:
            lines.append(cur_line)
            cur_line = [(idx_w, w, ww)]
            cur_w = ww
        else:
            cur_w = ww if not cur_line else cur_w + space_w + ww
            cur_line.append((idx_w, w, ww))
    if cur_line:
        lines.append(cur_line)

    cap_y = 1045
    line_h = 60
    cap_box_h = len(lines) * line_h + 36
    draw.rounded_rectangle(
        [SAFE_LEFT, cap_y - 18, SAFE_RIGHT, cap_y - 18 + cap_box_h],
        radius=18,
        fill=(10, 8, 24, int(175 * pop)),
        outline=acc_col + (int(120 * pop),),
        width=2,
    )

    cur_y = cap_y
    for line in lines:
        cur_x = SAFE_LEFT + 22
        for idx_w, w, ww in line:
            if idx_w == active_idx:
                draw.rounded_rectangle(
                    [cur_x - 8, cur_y - 4, cur_x + ww + 8, cur_y + 50],
                    radius=10,
                    fill=acc_col + (int(235 * pop),),
                )
                txt_col = (255, 255, 255, int(255 * pop))
            elif idx_w < active_idx:
                txt_col = (245, 245, 255, int(245 * pop))
            else:
                txt_col = (170, 176, 205, int(215 * pop))
            draw.text((cur_x, cur_y), w, font=FONT_CAPTION, fill=txt_col)
            cur_x += ww + space_w
        cur_y += line_h


# ---------------------------------------------------------------------------
# Main Frame Pipeline
# ---------------------------------------------------------------------------

def find_beat(beats, t):
    for idx, b in enumerate(beats):
        if b["start"] <= t < b["start"] + b["dur"]:
            return idx, b
    return len(beats) - 1, beats[-1]


def blend_mood_colors(beats, t):
    idx, beat = find_beat(beats, t)
    local_t = (t - beat["start"]) / max(0.01, beat["dur"])
    mood = beat.get("mood", "cool")
    c_top, c_bot, c_acc = MOOD_PALETTES.get(mood, MOOD_PALETTES["cool"])
    if idx + 1 < len(beats) and local_t > 0.7:
        nxt_mood = beats[idx + 1].get("mood", "cool")
        nxt_top, nxt_bot, nxt_acc = MOOD_PALETTES.get(nxt_mood, MOOD_PALETTES["cool"])
        blend = (local_t - 0.7) / 0.3
        return (
            lerp_color(c_top, nxt_top, blend),
            lerp_color(c_bot, nxt_bot, blend),
            lerp_color(c_acc, nxt_acc, blend),
        )
    return c_top, c_bot, c_acc


def render_frame(frame_idx, beats, total_dur, badge_prefix, particles, broll_cache):
    t = frame_idx / FPS
    idx, beat = find_beat(beats, t)
    beat_elapsed = t - beat["start"]
    mood = beat.get("mood", "cool")

    top_col, bot_col, acc_col = blend_mood_colors(beats, t)
    img = make_gradient_bg(top_col, bot_col)

    pulse = 0.92 + 0.08 * math.sin(t * 2.4 + idx)
    glow_size = int(680 * pulse)
    glow_layer = Image.new("RGBA", (glow_size, glow_size), acc_col + (0,))
    alpha_scale = 0.14 if mood == "dark" else (0.24 if mood in ("hook", "hero") else 0.18)
    mask = GLOW_MASK.resize((glow_size, glow_size), Image.Resampling.BILINEAR)
    mask = mask.point(lambda p: int(p * alpha_scale))
    glow_layer.putalpha(mask)
    gx = 460 - glow_size // 2 + int(25 * math.sin(t * 0.8))
    gy = 620 - glow_size // 2 + int(20 * math.cos(t * 0.6))
    img.paste(glow_layer, (gx, gy), glow_layer)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Subtle animated tech grid lines
    grid_shift = int((t * 18) % 90)
    for gx_line in range(SAFE_LEFT, WIDTH, 90):
        draw.line([(gx_line, 0), (gx_line, HEIGHT)], fill=(255, 255, 255, 10), width=1)
    for gy_line in range(grid_shift, HEIGHT, 90):
        draw.line([(0, gy_line), (WIDTH, gy_line)], fill=(255, 255, 255, 10), width=1)

    # Parallax particles
    speed_mult = 2.2 if mood in ("hook", "rise", "hero") else (0.7 if mood == "flat" else 1.2)
    for px0, py0, spd, sz, phase in particles:
        y = (py0 - t * 48.0 * spd * speed_mult) % HEIGHT
        x = (px0 + math.sin(t * 1.4 + phase) * 18.0) % WIDTH
        alpha = int(75 + 75 * math.sin(t * 3.0 + phase))
        draw.ellipse([x - sz, y - sz, x + sz, y + sz], fill=(255, 255, 255, alpha))

    # Top header inside safe zone
    badge = f"{badge_prefix}  //  BEAT {idx + 1:02d}/{len(beats)}"
    draw.text((SAFE_LEFT, SAFE_TOP + 8), badge, font=FONT_BADGE, fill=acc_col + (225,))
    draw.line([(SAFE_LEFT, SAFE_TOP + 50), (SAFE_RIGHT, SAFE_TOP + 50)],
              fill=(255, 255, 255, 55), width=2)

    if idx == 0:
        pop = 1.0
        slide_y = int(10 * math.sin(min(1.0, t * 4.0) * math.pi * 0.5))
    else:
        pop = ease_out_cubic(beat_elapsed / 0.20)
        slide_y = int(22 * (1.0 - pop))
    alpha_card = int(255 * pop)

    # Upper safe-zone headline card (y=318..520)
    card_lines = wrap_text(draw, beat["card"], FONT_CARD, MAX_TEXT_W - 28)[:2]
    card_top_y = 320 + slide_y
    cur_y = card_top_y
    for line in card_lines:
        h = draw.textbbox((0, 0), line, font=FONT_CARD)[3]
        draw.text((SAFE_LEFT + 31, cur_y + 4), line, font=FONT_CARD, fill=(0, 0, 0, alpha_card // 2))
        draw.text((SAFE_LEFT + 28, cur_y), line, font=FONT_CARD, fill=(255, 255, 255, alpha_card))
        cur_y += h + 10
    draw.rectangle(
        [SAFE_LEFT, card_top_y + 6, SAFE_LEFT + 8, max(card_top_y + 70, cur_y - 6)],
        fill=acc_col + (alpha_card,),
    )

    # Dynamic B-roll / mockup visual stage (y=550..875)
    vis = infer_visual(beat)
    vtype = vis.get("type", "metric")
    box = (SAFE_LEFT, 555, SAFE_RIGHT, 875)
    if vtype in ("broll", "image") and broll_cache.get(idx) is not None:
        draw_broll_visual(overlay, draw, box, broll_cache[idx], beat_elapsed, beat["dur"], acc_col)
    elif vtype == "terminal":
        draw_terminal_visual(draw, box, vis, beat_elapsed, beat["dur"], acc_col)
    elif vtype == "comparison":
        draw_comparison_visual(draw, box, vis, beat_elapsed, beat["dur"], acc_col)
    elif vtype == "ui_steps":
        draw_ui_steps_visual(draw, box, vis, beat_elapsed, beat["dur"], acc_col)
    else:
        draw_metric_visual(draw, box, vis, beat_elapsed, beat["dur"], acc_col)

    # Karaoke word-by-word captions (y=1025..1330)
    draw_karaoke_captions(draw, beat, beat_elapsed, pop, acc_col)

    # Bottom safe-zone timeline bar (y=1395)
    prog = min(1.0, t / total_dur)
    draw.rectangle([SAFE_LEFT, 1395, SAFE_RIGHT, 1403], fill=(255, 255, 255, 45))
    fill_prog_w = int(MAX_TEXT_W * prog)
    if fill_prog_w > 0:
        draw.rectangle([SAFE_LEFT, 1395, SAFE_LEFT + fill_prog_w, 1403], fill=acc_col + (235,))

    img = Image.alpha_composite(img, overlay)
    if t > total_dur - 0.6:
        fade = max(0.0, (total_dur - t) / 0.6)
        veil = Image.new("RGBA", (WIDTH, HEIGHT), (12, 8, 28, int(170 * (1.0 - fade))))
        img = Image.alpha_composite(img, veil)

    return img.convert("RGB").tobytes()


def resolve_music_path(music_name):
    if not music_name or music_name == "none":
        return None
    if os.path.exists(music_name):
        return music_name
    cand = os.path.join(MUSIC_DIR, music_name if music_name.endswith(".mp3") else f"{music_name}.mp3")
    return cand if os.path.exists(cand) else None


def render_from_spec(spec_path, output_path=None):
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    beats = spec["beats"]
    out_video = output_path or spec["output"]
    badge_prefix = spec.get("badge_prefix", "DEV TOOLS")
    voice = spec.get("voice", "en-US-AndrewMultilingualNeural")
    voice_rate = spec.get("voice_rate", "+8%")
    music_path = resolve_music_path(spec.get("music", "tech_pulse"))
    particles = init_particles(seed=spec.get("seed", 42))

    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Pre-load optional B-roll images
        broll_cache = {}
        for idx, b in enumerate(beats):
            vis = b.get("visual", {})
            if isinstance(vis, dict) and vis.get("type") in ("broll", "image"):
                src = vis.get("path") or vis.get("url")
                try:
                    broll_cache[idx] = load_broll_image(src, tmp_dir)
                except Exception:
                    broll_cache[idx] = None

        # 2. Generate AI voiceover & sync beat durations + WordBoundary timings
        vo_wav = None
        if spec.get("tts", True):
            vo_wav = synthesize_voiceover(beats, voice, voice_rate, tmp_dir)

        clock = 0.0
        for b in beats:
            b["start"] = round(clock, 3)
            clock = b["start"] + b["dur"]
        total_dur = clock
        total_frames = round(total_dur * FPS)

        raw_video = os.path.join(tmp_dir, "video_only.mp4")
        v_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            raw_video,
        ]
        proc = subprocess.Popen(v_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for frame in range(total_frames):
            proc.stdin.write(render_frame(frame, beats, total_dur, badge_prefix, particles, broll_cache))
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg video failed:\n{stderr}")

        # 3. Mix voiceover + background music into final MP4
        fade_st = max(0.2, total_dur - 1.2)
        if vo_wav and music_path:
            mix_cmd = [
                "ffmpeg", "-y",
                "-i", raw_video,
                "-i", vo_wav,
                "-stream_loop", "-1", "-i", music_path,
                "-filter_complex",
                f"[1:a]volume=1.15[vo];[2:a]volume=0.16,afade=t=in:st=0:d=0.4,afade=t=out:st={fade_st:.2f}:d=1.2[bg];[vo][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-shortest", "-movflags", "+faststart",
                out_video,
            ]
        elif vo_wav:
            mix_cmd = [
                "ffmpeg", "-y", "-i", raw_video, "-i", vo_wav,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-shortest", "-movflags", "+faststart",
                out_video,
            ]
        elif music_path:
            mix_cmd = [
                "ffmpeg", "-y", "-i", raw_video, "-stream_loop", "-1", "-i", music_path,
                "-filter_complex", f"[1:a]volume=0.35,afade=t=in:st=0:d=0.4,afade=t=out:st={fade_st:.2f}:d=1.2[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-shortest", "-movflags", "+faststart",
                out_video,
            ]
        else:
            mix_cmd = ["ffmpeg", "-y", "-i", raw_video, "-c", "copy", "-movflags", "+faststart", out_video]

        subprocess.run(mix_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return out_video, total_dur


def main():
    ap = argparse.ArgumentParser(description="Render a Video Engine v2 Instagram Reel from JSON spec.")
    ap.add_argument("spec", help="Path to JSON beat specification")
    ap.add_argument("--output", help="Override output MP4 path")
    args = ap.parse_args()
    out, dur = render_from_spec(args.spec, args.output)
    size_mb = os.path.getsize(out) / (1024 * 1024)
    print(f"Rendered {dur:.1f}s -> {out} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
