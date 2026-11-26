# Instagram Automation (`@factbaazii`)

Automated 2× daily faceless Instagram Reels pipeline powered by GitHub Actions, FFmpeg + Pillow, `instagram-agent` validators (MIT), and Buffer GraphQL API.

## Schedule
- **11:30 AM IST** (`0 6 * * *` UTC)
- **6:00 PM IST** (`30 12 * * *` UTC)

## How it works
1. Checks `queue/*.json` for pre-validated Reels; if empty, generates a fresh Reel using `GEMINI_API_KEY` or `GROQ_API_KEY` grounded in `data/voice.md`, `data/log.md`, and `validators/hooks.json`.
2. Validates hooks (`hookscore.py`), beat timing & loop (`beats.py`), humanization (`humanize.py` + `detect.py`), and caption (`caption.py`).
3. Renders a `1080×1920` H.264 MP4 (`scripts/render_reel.py`), uploads it to the `reels-media` GitHub Release, and publishes via Buffer (`scripts/publish_reel.py`).
4. Logs the published post to `data/log.md` and archives used queue files in `posted/`.

## Required GitHub Repository Secrets
- `BUFFER_API_KEY`
- `BUFFER_CHANNEL_ID`
- Optional for infinite auto-generation after `queue/` empties: `GEMINI_API_KEY` (Google AI Studio free tier) or `GROQ_API_KEY` (Groq free tier)
