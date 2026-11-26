#!/usr/bin/env python3
"""
Daily GitHub Actions orchestrator for @factbaazii Instagram Reels.

1. Uses the next pre-validated spec in queue/*.json if available.
2. Otherwise generates a fresh Reel spec using GEMINI_API_KEY or GROQ_API_KEY
   (with data/voice.md + data/log.md + validators/hooks.json) and validates it
   against hookscore.py, beats.py, humanize.py, detect.py, and caption.py.
3. Renders the 1080x1920 MP4, uploads to GitHub Releases, publishes via Buffer,
   moves completed queue specs to posted/, and logs to data/log.md.
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALIDATORS = os.path.join(REPO_ROOT, "validators")
DATA_DIR = os.path.join(REPO_ROOT, "data")
QUEUE_DIR = os.path.join(REPO_ROOT, "queue")
POSTED_DIR = os.path.join(REPO_ROOT, "posted")
TMP_DIR = os.path.join(REPO_ROOT, "tmp")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_reel import render_from_spec
from publish_reel import load_config, upload_to_github_release, publish_reel


def run_py(script_name, args, stdin_text=None):
    cmd = [sys.executable, os.path.join(VALIDATORS, script_name)] + args
    return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True)


def validate_spec(spec):
    """Validate script & caption against hookscore, beats, ig-human, and caption.py."""
    beats = spec["beats"]
    lines = [b["caption"].strip() for b in beats if b.get("caption", "").strip()]
    if not lines:
        return False, "empty beats"

    # 1. Hook score >= 70
    hook_res = run_py("hookscore.py", ["--hook", lines[0], "--json"])
    if hook_res.returncode not in (0, 1):
        return False, f"hookscore failed: {hook_res.stderr}"
    hook_data = json.loads(hook_res.stdout)
    if hook_data["score"] < 70:
        return False, f"hook score {hook_data['score']} < 70 ({hook_data['weakest']})"

    # 2. Beats timing & loop check
    script_text = "\n".join(lines) + "\n"
    beats_res = run_py("beats.py", ["-", "--json"], stdin_text=script_text)
    if beats_res.returncode != 0:
        return False, f"beats failed: {beats_res.stderr}"
    beats_data = json.loads(beats_res.stdout)
    for b in beats_data["beats"]:
        if b["flags"]:
            return False, f"beat {b['n']} flagged: {b['flags']}"

    with tempfile.TemporaryDirectory() as td:
        s_in = os.path.join(td, "script.txt")
        s_clean = os.path.join(td, "script_clean.txt")
        c_in = os.path.join(td, "caption.txt")
        c_clean = os.path.join(td, "caption_clean.txt")
        open(s_in, "w", encoding="utf-8").write(script_text)
        open(c_in, "w", encoding="utf-8").write(spec["caption"].strip() + "\n")

        run_py("humanize.py", [s_in, "-o", s_clean])
        run_py("humanize.py", [c_in, "-o", c_clean])

        # Update spec with cleaned text
        cleaned_lines = [l.strip() for l in open(s_clean, encoding="utf-8").read().splitlines() if l.strip()]
        if len(cleaned_lines) == len(beats):
            for b, cl in zip(beats, cleaned_lines):
                b["caption"] = cl
        spec["caption"] = open(c_clean, encoding="utf-8").read().strip()

        d_script = run_py("detect.py", [s_clean, "--json"])
        d_cap = run_py("detect.py", [c_clean, "--json"])
        ds = json.loads(d_script.stdout)
        dc = json.loads(d_cap.stdout)
        if ds["verdict"] != "PASS":
            weak_s = min(ds["checks"], key=lambda k: ds["checks"][k]["score"])
            return False, f"script detect {ds['human_score']} {ds['verdict']} (weakest: {weak_s})"
        if dc["verdict"] != "PASS":
            weak_c = min(dc["checks"], key=lambda k: dc["checks"][k]["score"])
            return False, f"caption detect {dc['human_score']} {dc['verdict']} (weakest: {weak_c})"

        kw = ",".join(spec.get("keywords", []))
        cap_args = [c_clean, "--json"] + (["--keywords", kw] if kw else [])
        cap_res = run_py("caption.py", cap_args)
        cap_data = json.loads(cap_res.stdout)
        if cap_data["verdict"] == "FIX":
            fails = [c["check"] + ": " + c["detail"] for c in cap_data["checks"] if c["status"] == "FAIL"]
            return False, f"caption lint FIX: {fails}"

    return True, f"hook={hook_data['score']} script={ds['human_score']} caption={dc['human_score']} cap_lint={cap_data['verdict']}"


def call_llm(prompt):
    """Generate a JSON Reel spec via GEMINI_API_KEY or GROQ_API_KEY."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()

    if gemini_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8},
        }
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)

    if groq_key:
        url = "https://api.groq.com/openai/v1/chat/completions"
        body = {
            "model": "openai/gpt-oss-120b",
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {groq_key}"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        return json.loads(data["choices"][0]["message"]["content"])

    raise RuntimeError("Queue is empty and neither GEMINI_API_KEY nor GROQ_API_KEY is set in GitHub Secrets.")


def generate_with_llm(feedback=""):
    voice = open(os.path.join(DATA_DIR, "voice.md"), encoding="utf-8").read()
    log = open(os.path.join(DATA_DIR, "log.md"), encoding="utf-8").read()
    hooks = json.load(open(os.path.join(VALIDATORS, "hooks.json"), encoding="utf-8"))["hooks"]
    hook_summary = "\n".join(f"#{h['id']} {h['name']}: {h['template']}" for h in hooks[:20])

    prompt = f"""You are producing one faceless Instagram Reel for @factbaazii.
VOICE PROFILE:
{voice}

RECENT POST LOG (DO NOT repeat these topics or use the same hook formula as the last entry):
{log[-2500:]}

HOOK FORMULAS:
{hook_summary}

REQUIREMENTS:
- Pick ONE real, legitimate free AI tool for students/developers, free alternative to a paid tool, or free-tier API/MCP trick with real verifiable pricing/limits (never pirated or paywall-bypassing).
- Total Reel length 30-42s across 12-15 beats.
- Beat 1 caption MUST score >=70 on hookscore (5-9 words, concrete number/price, tension/stakes, addresses 'you'/'your', no greeting).
- Each beat caption <=11 words (max 4s at 165 wpm). Mix 3-word punch lines and 9-11 word lines so sentence-length variation is >=0.55. Include contractions (don't, you're, it's) and concrete digits.
- Last beat caption MUST echo a key word from Beat 1 for a seamless loop.
- Each beat 'card' MUST be <=5 words ALL CAPS (Beat 1 card <=4 words for grid readability).
- 'mood' must be one of: hook, cool, dark, rise, flat, hero, loop.
- 'caption' is a Job A Instagram caption: <=125-char concrete first line with an ask ('Save this...'), short paragraphs containing all 3 search 'keywords', 3 targeted hashtags at the end, 2+ contractions, zero em dashes, zero slop words.
{('FIX THIS FROM PREVIOUS ATTEMPT: ' + feedback) if feedback else ''}
Return ONLY valid JSON with keys:
topic, pillar, hook_formula, badge_prefix, keywords (3 strings), caption, beats (list of {{dur, card, caption, mood}})."""
    return call_llm(prompt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(POSTED_DIR, exist_ok=True)

    queued_files = sorted(f for f in os.listdir(QUEUE_DIR) if f.endswith(".json"))
    source_file = None
    spec = None

    if queued_files:
        source_file = os.path.join(QUEUE_DIR, queued_files[0])
        print(f"Using queued Reel: {queued_files[0]}")
        spec = json.load(open(source_file, encoding="utf-8"))
        ok, info = validate_spec(spec)
        if not ok:
            raise RuntimeError(f"Queued spec failed validation: {info}")
        print(f"Validated queued spec: {info}")
    else:
        feedback = ""
        for attempt in range(1, 4):
            print(f"Generating fresh Reel via LLM (attempt {attempt}/3)...")
            candidate = generate_with_llm(feedback)
            ok, info = validate_spec(candidate)
            print(f"  Validation: ok={ok} ({info})")
            if ok:
                spec = candidate
                break
            feedback = info
        if not spec:
            raise RuntimeError(f"Failed to generate a passing Reel after 3 attempts: {feedback}")

    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    stamp = datetime.datetime.now(ist).strftime("%Y%m%d_%H%M")
    mp4_path = os.path.join(TMP_DIR, f"reel_{stamp}.mp4")
    cap_path = os.path.join(TMP_DIR, f"caption_{stamp}.txt")
    spec_path = os.path.join(TMP_DIR, f"spec_{stamp}.json")

    json.dump(spec, open(spec_path, "w", encoding="utf-8"), indent=2)
    open(cap_path, "w", encoding="utf-8").write(spec["caption"].strip() + "\n")

    out_mp4, dur = render_from_spec(spec_path, mp4_path)
    print(f"Rendered {dur:.1f}s -> {out_mp4}")

    cfg = load_config()
    repo = cfg.get("GITHUB_REPO", "").strip()
    if not repo:
        raise RuntimeError("Missing GITHUB_REPO / GITHUB_REPOSITORY")

    if args.dry_run:
        res = publish_reel(cfg, f"https://example.com/{os.path.basename(out_mp4)}", spec["caption"], dry_run=True)
        print("DRY RUN:", json.dumps(res, indent=2))
        return

    video_url = upload_to_github_release(out_mp4, repo)
    res = publish_reel(cfg, video_url, spec["caption"], mode="shareNow", dry_run=False)
    print("Buffer response:", json.dumps(res, indent=2))

    post_obj = res.get("data", {}).get("createPost", {})
    if "message" in post_obj:
        raise RuntimeError(f"Buffer createPost error: {post_obj['message']}")
    post_id = post_obj.get("post", {}).get("id", "unknown")

    if source_file:
        shutil.move(source_file, os.path.join(POSTED_DIR, os.path.basename(source_file)))

    now_str = datetime.datetime.now(ist).strftime("%Y-%m-%d %H:%M IST")
    first_line = spec["beats"][0]["caption"]
    cover = spec["beats"][0]["card"]
    log_entry = (
        f"\n- **{now_str}** | **Reel** (Faceless, {dur:.1f}s, AUTO) | "
        f"**Pillar:** {spec.get('pillar', 'Teach')} | **Topic:** {spec['topic']} | "
        f"**Hook:** `{spec.get('hook_formula', 'Auto')}` | **First line:** \"{first_line}\" | "
        f"**On-screen:** `{cover}` | **Buffer Post ID:** `{post_id}`\n"
    )
    with open(os.path.join(DATA_DIR, "log.md"), "a", encoding="utf-8") as fh:
        fh.write(log_entry)
    print("Logged to data/log.md")


if __name__ == "__main__":
    main()
