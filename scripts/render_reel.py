#!/usr/bin/env python3
"""
Parameterized 1080x1920 30fps faceless Instagram Reel renderer.

Works both on Android/Termux and Ubuntu GitHub Actions runners.
Pipes raw RGB frames directly to ffmpeg inside the IG safe zone
(y=240..1430, x=90..835).
"""

import argparse
import json
import math
import os
import random
import subprocess
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1920
FPS = 30
SAFE_LEFT = 90
SAFE_RIGHT = 835
SAFE_TOP = 240
MAX_TEXT_W = SAFE_RIGHT - SAFE_LEFT

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_FONTS = os.path.join(REPO_ROOT, "fonts")

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
], 92)

FONT_CAPTION = load_font([
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-Medium.ttf"),
    "/system/fonts/BarlowSemiCondensed-Medium.ttf",
    "/system/fonts/BarlowSemiCondensed-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/system/fonts/DroidSans.ttf",
], 46)

FONT_BADGE = load_font([
    os.path.join(LOCAL_FONTS, "BarlowSemiCondensed-SemiBold.ttf"),
    "/system/fonts/BarlowSemiCondensed-SemiBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/system/fonts/DroidSans-Bold.ttf",
], 30)


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


def draw_text_block(draw, lines, x, y, font, fill, line_gap=14, shadow_alpha=120):
    cur_y = y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        h = bbox[3] - bbox[1]
        if shadow_alpha > 0:
            draw.text((x + 3, cur_y + 4), line, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, cur_y), line, font=font, fill=fill)
        cur_y += h + line_gap
    return cur_y


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


def render_frame(frame_idx, beats, total_dur, badge_prefix, particles):
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
    gy = 700 - glow_size // 2 + int(20 * math.cos(t * 0.6))
    img.paste(glow_layer, (gx, gy), glow_layer)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    speed_mult = 2.2 if mood in ("hook", "rise", "hero") else (0.7 if mood == "flat" else 1.2)
    for px0, py0, spd, sz, phase in particles:
        y = (py0 - t * 48.0 * spd * speed_mult) % HEIGHT
        x = (px0 + math.sin(t * 1.4 + phase) * 18.0) % WIDTH
        alpha = int(75 + 75 * math.sin(t * 3.0 + phase))
        draw.ellipse([x - sz, y - sz, x + sz, y + sz], fill=(255, 255, 255, alpha))

    badge = f"{badge_prefix}  //  BEAT {idx + 1:02d}/{len(beats)}"
    draw.text((SAFE_LEFT, SAFE_TOP + 8), badge, font=FONT_BADGE, fill=acc_col + (225,))
    draw.line([(SAFE_LEFT, SAFE_TOP + 54), (SAFE_RIGHT, SAFE_TOP + 54)],
              fill=(255, 255, 255, 55), width=2)

    if idx == 0:
        pop = 1.0
        slide_y = int(12 * math.sin(min(1.0, t * 4.0) * math.pi * 0.5))
    else:
        pop = ease_out_cubic(beat_elapsed / 0.22)
        slide_y = int(28 * (1.0 - pop))
    alpha_card = int(255 * pop)

    card_lines = wrap_text(draw, beat["card"], FONT_CARD, MAX_TEXT_W - 28)
    card_top_y = 520 + slide_y
    end_card_y = draw_text_block(
        draw, card_lines, SAFE_LEFT + 28, card_top_y,
        FONT_CARD, (255, 255, 255, alpha_card), line_gap=14, shadow_alpha=alpha_card // 2,
    )
    draw.rectangle(
        [SAFE_LEFT, card_top_y + 8, SAFE_LEFT + 8, max(card_top_y + 80, end_card_y - 8)],
        fill=acc_col + (alpha_card,),
    )

    cap_lines = wrap_text(draw, beat["caption"], FONT_CAPTION, MAX_TEXT_W - 44)
    cap_y = 1030
    cap_box_h = len(cap_lines) * 58 + 36
    draw.rounded_rectangle(
        [SAFE_LEFT, cap_y - 18, SAFE_RIGHT, cap_y - 18 + cap_box_h],
        radius=18,
        fill=(10, 8, 24, int(155 * pop)),
        outline=acc_col + (int(110 * pop),),
        width=2,
    )
    draw_text_block(
        draw, cap_lines, SAFE_LEFT + 22, cap_y,
        FONT_CAPTION, (235, 235, 248, int(245 * pop)), line_gap=12, shadow_alpha=0,
    )

    prog = min(1.0, t / total_dur)
    draw.rectangle([SAFE_LEFT, 1395, SAFE_RIGHT, 1403], fill=(255, 255, 255, 45))
    fill_prog_w = int(MAX_TEXT_W * prog)
    if fill_prog_w > 0:
        draw.rectangle([SAFE_LEFT, 1395, SAFE_LEFT + fill_prog_w, 1403], fill=acc_col + (235,))

    img = Image.alpha_composite(img, overlay)
    if t > total_dur - 0.7:
        fade = max(0.0, (total_dur - t) / 0.7)
        veil = Image.new("RGBA", (WIDTH, HEIGHT), (12, 8, 28, int(180 * (1.0 - fade))))
        img = Image.alpha_composite(img, veil)

    return img.convert("RGB").tobytes()


def render_from_spec(spec_path, output_path=None):
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    beats = spec["beats"]
    clock = 0.0
    for b in beats:
        b["start"] = round(clock, 3)
        clock = b["start"] + b["dur"]
    total_dur = clock
    total_frames = round(total_dur * FPS)
    out_video = output_path or spec["output"]
    badge_prefix = spec.get("badge_prefix", "DEV TOOLS")
    particles = init_particles(seed=spec.get("seed", 42))

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        out_video,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for frame in range(total_frames):
        proc.stdin.write(render_frame(frame, beats, total_dur, badge_prefix, particles))
    proc.stdin.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed:\n{stderr}")
    return out_video, total_dur


def main():
    ap = argparse.ArgumentParser(description="Render a faceless Instagram Reel from JSON spec.")
    ap.add_argument("spec", help="Path to JSON beat specification")
    ap.add_argument("--output", help="Override output MP4 path")
    args = ap.parse_args()
    out, dur = render_from_spec(args.spec, args.output)
    size_mb = os.path.getsize(out) / (1024 * 1024)
    print(f"Rendered {dur:.1f}s -> {out} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
