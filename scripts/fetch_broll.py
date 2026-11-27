#!/usr/bin/env python3
"""
Free B-roll fetcher (Pexels / Pixabay) for vertical 9:16 Reels.

Reads PEXELS_API_KEY or PIXABAY_API_KEY from environment or ~/.claude/instagram/secrets.env.
Downloads vertical video clips or photos and extracts/crops them for the Reel stage.
"""

import argparse
import json
import os
import subprocess
import urllib.parse
import urllib.request

SECRETS_PATH = os.path.expanduser("~/.claude/instagram/secrets.env")


def load_keys():
    keys = {}
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    for k in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
        if os.environ.get(k):
            keys[k] = os.environ[k].strip()
    return keys


def fetch_pexels_media(query, api_key, media_type="videos"):
    q = urllib.parse.quote(query)
    if media_type == "videos":
        url = f"https://api.pexels.com/videos/search?query={q}&orientation=portrait&per_page=3"
    else:
        url = f"https://api.pexels.com/v1/search?query={q}&orientation=portrait&per_page=3"
    req = urllib.request.Request(url, headers={"Authorization": api_key, "User-Agent": "ig-studio/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    if media_type == "videos":
        for v in data.get("videos", []):
            files = sorted(v.get("video_files", []), key=lambda x: x.get("height", 0), reverse=True)
            for vf in files:
                if vf.get("link") and vf.get("width", 0) <= 1440:
                    return vf["link"], "mp4"
    else:
        for p in data.get("photos", []):
            src = p.get("src", {}).get("large2x") or p.get("src", {}).get("large")
            if src:
                return src, "jpg"
    return None, None


def fetch_pixabay_media(query, api_key, media_type="videos"):
    q = urllib.parse.quote(query)
    if media_type == "videos":
        url = f"https://pixabay.com/api/videos/?key={api_key}&q={q}&per_page=3"
    else:
        url = f"https://pixabay.com/api/?key={api_key}&q={q}&orientation=vertical&per_page=3"
    req = urllib.request.Request(url, headers={"User-Agent": "ig-studio/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    for hit in data.get("hits", []):
        if media_type == "videos":
            v = hit.get("videos", {}).get("medium") or hit.get("videos", {}).get("small")
            if v and v.get("url"):
                return v["url"], "mp4"
        else:
            src = hit.get("largeImageURL")
            if src:
                return src, "jpg"
    return None, None


def download_broll(query, out_dir, width=745, height=320):
    """Fetch a portrait image or clip and normalize it to fit the visual stage."""
    os.makedirs(out_dir, exist_ok=True)
    keys = load_keys()
    url, ext = None, None
    if keys.get("PEXELS_API_KEY"):
        url, ext = fetch_pexels_media(query, keys["PEXELS_API_KEY"], "photos")
        if not url:
            url, ext = fetch_pexels_media(query, keys["PEXELS_API_KEY"], "videos")
    elif keys.get("PIXABAY_API_KEY"):
        url, ext = fetch_pixabay_media(query, keys["PIXABAY_API_KEY"], "photos")
        if not url:
            url, ext = fetch_pixabay_media(query, keys["PIXABAY_API_KEY"], "videos")
    if not url:
        return None

    slug = "".join(c if c.isalnum() else "_" for c in query.lower())[:32]
    raw_path = os.path.join(out_dir, f"{slug}_raw.{ext}")
    final_path = os.path.join(out_dir, f"{slug}.{ext}")
    req = urllib.request.Request(url, headers={"User-Agent": "ig-studio/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        open(raw_path, "wb").write(r.read())

    if ext == "mp4":
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-t", "5", "-vf",
             f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
             "-an", "-c:v", "libx264", "-preset", "fast", final_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.remove(raw_path)
    else:
        os.replace(raw_path, final_path)
    return final_path


def main():
    ap = argparse.ArgumentParser(description="Fetch free B-roll via Pexels or Pixabay API.")
    ap.add_argument("query")
    ap.add_argument("--out-dir", default="tmp/broll")
    args = ap.parse_args()
    res = download_broll(args.query, args.out_dir)
    if not res:
        print("No PEXELS_API_KEY / PIXABAY_API_KEY found or no match.")
        return
    print("Downloaded:", res)


if __name__ == "__main__":
    main()
