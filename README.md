# transcriber

**Turn one long podcast video into short vertical clips with subtitles — ready for Instagram, TikTok, and YouTube Shorts.** You choose how many clips (5 by default, or 10, 20, as many as you want).

- **You give it:** a long podcast video (the wide, landscape kind).
- **You get back:** a handful of phone-shaped clips of the best moments, with animated word-by-word subtitles burned in — plus ready-to-paste captions.

It runs entirely on your own Mac. No accounts, no paid AI services, nothing uploaded anywhere.

---

## ⚠️ First — does your computer work for this?

**You need an Apple Silicon Mac** — any Mac with an **M1, M2, M3, or M4** chip (basically any Mac from late 2020 onward).

Check in 10 seconds:
1. Click the  Apple logo, top-left of your screen.
2. Click **About This Mac**.
3. Look at **Chip**:
   - Says **Apple M1 / M2 / M3 / M4** → ✅ you're good.
   - Says **Intel** → ❌ it won't run as-is. See [Not on an Apple Silicon Mac?](#not-on-an-apple-silicon-mac) at the bottom.

You'll also want ~5 GB free disk space, and about an hour (mostly hands-off) for a 2-hour podcast. The first run also downloads the speech model (~1.5 GB, one time).

---

## The easy way: let Claude Code do the work

This project is built to be run by **talking to [Claude Code](https://docs.claude.com/en/docs/claude-code)** (the AI coding assistant). You barely touch the Terminal — you just tell Claude what you want.

### One-time setup

1. **Install Claude Code** — follow the official guide: https://docs.claude.com/en/docs/claude-code (install it, sign in).
2. **Download this project:**
   - On the GitHub page, click the green **Code** button → **Download ZIP**.
   - Unzip it, and drag the folder somewhere easy like your Desktop.
3. **Open that folder in Claude Code** and type:
   ```
   run the setup script
   ```
   Claude runs `./setup.sh` for you — it checks your Mac, installs the video tool (ffmpeg), and sets up the rest. Wait until it says **Setup complete**. You only do this once.

### Make your reels

1. Put your podcast video somewhere easy (drag it onto your Desktop).
2. In Claude Code, type (drag the video into the chat to get its exact path):
   ```
   /clip ~/Desktop/my-podcast.mp4
   ```
3. **Wait.** Claude listens to the whole podcast, picks the best moments (5 by default — or however many you ask for), writes English subtitles if needed, cuts them, follows whoever's talking, adds the animated subtitles, and saves the finished videos. ~1 hour for a 2-hour podcast, mostly unattended.
4. **Done.** Your finished clips are in:
   ```
   projects/<your-podcast-name>/6_reels/
   ```
   and Instagram captions are written for you in `7_captions.json` in the same folder. 🎉

### Handy things you can ask for

| You want… | Type this |
|---|---|
| Skip the intro/ads/a boring stretch | `/clip ~/Desktop/my-podcast.mp4 --exclude 0:00-2:30,55:10-58:00` |
| How many clips (any number; 5 by default) | `/clip ~/Desktop/my-podcast.mp4 --n 10` |
| Approve the chosen moments before it builds | `/clip ~/Desktop/my-podcast.mp4 --review-picks` |

---

## What you get (the folders)

After a run, inside `projects/<name>/`:

| File / folder | What's in it |
|---|---|
| `6_reels/` | ✅ **Your finished vertical videos.** This is the payoff. |
| `7_captions.json` | Ready-to-paste Instagram captions + hashtags. |
| `7_covers/` | A cover/thumbnail image per clip. |
| `2_picks.json` | Which moments were chosen — edit it and rerun to change them. |
| `2_translations.json` | The English subtitle text — edit it and rerun to fix wording. |

Tweak the picks or the wording, then just ask Claude to "rebuild the reels" — it only redoes what changed.

---

## What it does, step by step (for the curious)

1. **Transcribe** — writes down every word of the podcast with timestamps.
2. **Pick clips** — scores moments for hooks, emotion, strong opinions, and stories, then picks the best handful.
3. **Translate** — if the podcast isn't in English, writes natural English subtitles.
4. **Cut** — slices those moments out of the original.
5. **Reframe** — turns wide (16:9) into tall (9:16), auto-zooming and cutting to whoever is speaking.
6. **Subtitle** — adds karaoke-style, word-by-word subtitles.
7. **Burn** — bakes it all into the final MP4s.

Full architecture and all the tuning knobs live in [CLAUDE.md](CLAUDE.md).

---

## Prefer the Terminal? (optional)

You don't need this if you're using Claude Code, but every stage also runs by hand:

```bash
./setup.sh                          # one-time
source venv/bin/activate
python pipeline.py my_podcast       # runs the whole pipeline
```

Each stage is its own module (`python -m src.transcribe --project my_podcast`, etc.) — see [CLAUDE.md](CLAUDE.md) for the per-stage commands and how to re-run just one stage.

---

## Not on an Apple Silicon Mac?

The transcribe step uses [`mlx-whisper`](https://github.com/ml-explore/mlx-examples), which only runs on Apple Silicon. On an Intel Mac / Windows / Linux it won't work as-is — but everything else (cutting, face-tracking, subtitles) already runs anywhere.

The fix is to swap the transcriber for a cross-platform one like [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper). If that's you: open the folder in Claude Code and ask it to *"swap mlx-whisper for faster-whisper so this runs on my machine."*

---

## License

[MIT](LICENSE) — do whatever you want with it.
