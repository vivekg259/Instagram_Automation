#!/usr/bin/env python3
"""
Upload a rendered MP4 to a public GitHub Release and publish it as an Instagram
Reel via Buffer's GraphQL API.

Reads BUFFER_API_KEY, BUFFER_CHANNEL_ID, and GITHUB_REPOSITORY from environment
variables first, or falls back to ~/.claude/instagram/secrets.env for local runs.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request

SECRETS_PATH = os.path.expanduser("~/.claude/instagram/secrets.env")
BUFFER_ENDPOINT = "https://api.buffer.com"

CREATE_POST_MUTATION = """
mutation CreateInstagramReel($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post {
        id
        status
        dueAt
        text
      }
    }
    ... on MutationError {
      message
    }
  }
}
"""


def load_config():
    cfg = {}
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    for key in ("BUFFER_API_KEY", "BUFFER_CHANNEL_ID", "GITHUB_REPO", "GITHUB_REPOSITORY"):
        val = os.environ.get(key)
        if val:
            cfg[key] = val.strip()
    if "GITHUB_REPO" not in cfg or not cfg["GITHUB_REPO"]:
        cfg["GITHUB_REPO"] = cfg.get("GITHUB_REPOSITORY", "")
    return cfg


def buffer_graphql(api_key, query, variables=None):
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    req = urllib.request.Request(
        BUFFER_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "ig-auto/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_to_github_release(video_path, repo, tag="reels-media"):
    filename = os.path.basename(video_path)
    check = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        subprocess.run(
            ["gh", "release", "create", tag, "--repo", repo,
             "--title", "Reels Media", "--notes", "Automated Reel assets"],
            check=True,
        )
    subprocess.run(
        ["gh", "release", "upload", tag, video_path, "--repo", repo, "--clobber"],
        check=True,
    )
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def publish_reel(cfg, video_url, caption, mode="shareNow", dry_run=False):
    variables = {
        "input": {
            "channelId": cfg["BUFFER_CHANNEL_ID"],
            "text": caption,
            "schedulingType": "automatic",
            "mode": mode,
            "needsApproval": False,
            "assets": [{"video": {"url": video_url}}],
            "metadata": {
                "instagram": {
                    "type": "reel",
                    "shouldShareToFeed": True,
                }
            },
        }
    }
    if dry_run:
        return {"dry_run": True, "variables": variables}
    return buffer_graphql(cfg["BUFFER_API_KEY"], CREATE_POST_MUTATION, variables)


def main():
    ap = argparse.ArgumentParser(description="Upload Reel MP4 to GitHub and publish via Buffer.")
    ap.add_argument("--video", required=True)
    ap.add_argument("--caption-file", required=True)
    ap.add_argument("--mode", default="shareNow", choices=["shareNow", "addToQueue", "shareNext"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    repo = cfg.get("GITHUB_REPO", "").strip()
    if not repo:
        print("Missing GITHUB_REPO / GITHUB_REPOSITORY", file=sys.stderr)
        sys.exit(2)
    with open(args.caption_file, encoding="utf-8") as fh:
        caption = fh.read().strip()

    video_url = upload_to_github_release(args.video, repo)
    res = publish_reel(cfg, video_url, caption, mode=args.mode, dry_run=args.dry_run)
    print(json.dumps({"video_url": video_url, "buffer": res}, indent=2))


if __name__ == "__main__":
    main()
