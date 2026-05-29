---
name: clip
description: End-to-end podcast → 4-6 vertical reels with karaoke subs, face-tracked, covers, and IG captions. User invokes /clip <path-to-mp4> with optional --exclude HH:MM-HH:MM,... and --n N. Bootstraps a project, transcribes, lets Claude re-rank candidates against a virality taxonomy, writes EN translations inline if the source is non-English, then runs cut → reframe → subtitle → burn → cover and finally writes IG captions. Resumable — re-running on the same path skips stages whose outputs already exist.
---

# /clip — podcast → reels orchestrator

## User intent

User typed `/clip <path>` and wants 4-6 ready-to-post vertical reels at `projects/<slug>/6_reels/`. Optional flags:

- `--exclude 13:57-16:56,20:51-23:28,42:51-end` — skip already-clipped regions
- `--n 5` — target clip count (default 5; 3-6 is typical)
- `--review-picks` — pause after step 5 for the user to edit `2_picks.json`
- `--review-translations` — pause after step 6 for the user to edit `2_translations.json`

If they did not provide a `.mp4` path, ask once. Don't ask anything else — the skill picks reasonable defaults.

## Pipeline — 13 steps. Run them in order.

**Each deterministic stage is idempotent.** If an output already exists the module skips it, so re-running `/clip` on the same path is cheap.

### 1. Bootstrap

```bash
python scripts/run_clip.py bootstrap "<path>" [--exclude WINDOWS] [--n N]
```

Captures the printed `SLUG=<slug>`. Use this slug for every subsequent command. The bootstrap script will:
- slugify the filename (drops `PODCAST #N`, romanizes Estonian ÄÖÜÕ → A/O/Y)
- mkdir `projects/<slug>/`
- symlink source.mp4 (use a small Python helper if Bash refuses `/Volumes/...` paths — has happened)
- write `project.json` with `n_clips` + optional `exclude_windows`

**If the source is on an external/removable volume (`/Volumes/...`), test that it's actually readable before transcribing:** `dd if="<path>" of=/dev/null bs=1 count=10`. If that fails with `Operation not permitted` *even with the sandbox disabled*, it's macOS **TCC** (privacy) blocking the app from the volume — not the sandbox, and no symlink fixes it. Don't tell the user to grant Full Disk Access + restart the app (that kills the session). Instead drive **Finder** (which already has volume access) to copy the file locally, then re-point the symlink — see the TCC failure-mode below. Verify with `ffmpeg -i source.mp4 -t 1 ...` before kicking off the long transcribe.

### 2. Transcribe (long — run in background)

```bash
python -m src.transcribe --project <slug>
```

~30-60 min for a 2 h podcast. Use `run_in_background: true`. Transcribe auto-feeds whisper an `initial_prompt` built from `project.json` `title` (guest name) + optional `terms` array, so names/loanwords transcribe correctly. If the user already knows key names/jargon, add them to `terms` in `project.json` *before* this step for best accuracy. If the user checks in, confirm progress by tailing the log:
```bash
LC_ALL=C tail -c 4000 projects/<slug>/pipeline.log | LC_ALL=C tr '\r' '\n' | tail -20
```

### 3. Detect language

```bash
LANG=$(python -m src.lang_detect --project <slug>)
```

Updates `project.json` `language` field. If `LANG == en`, you SKIP the translation step (step 6). The Estonian-language fallback in `src/translate.py` will be used directly.

### 4. Heuristic candidates + initial picks

```bash
python -m src.select_clips --project <slug>
```

Writes `2_candidates.json` (top 50) and `2_picks.json` (initial top-N picks, already excluding `exclude_windows`). This is just a starting point — you replace it in step 5.

### 5. **Claude re-ranks against the virality taxonomy**

This is the most important quality lever. **Don't skip it.**

Read `projects/<slug>/2_candidates.json` (top 50 by heuristic) AND `projects/<slug>/1_transcript.json` (for fuller context if a preview is ambiguous).

**The heuristic candidates cluster** — the top 50 are usually near-duplicate windows of the same 2-3 hot moments, so picking from them alone yields overlapping, repetitive clips (especially as `--n` grows). Instead **build a condensed timeline of the whole transcript** and scan it end-to-end for N *distinct* moments spread across the episode:
```python
# merge segments into ~25s blocks → ~one readable line per block
segs = json.load(open(f"projects/{slug}/1_transcript.json"))["segments"]
# emit "[MM:SS-MM:SS | start_sec] text..." per block, then read it and hand-pick
```
Use the candidate scores as a hint, but choose windows for *variety* across the taxonomy and across the runtime. Snap each window so the hook/punchline isn't clipped at the edge (verify the payoff line falls fully inside; extend the end a beat if needed).

Score each candidate against these 8 dimensions. A strong pick scores high on **at least 2-3**:

| Dimension | What to look for |
|---|---|
| **Hook** | Opens with a surprising claim or grab |
| **Emotional peak** | Laughter, anger, vulnerability, awe |
| **Opinion** | Strong personal stance, "most people are wrong about X" |
| **Revelation** | Backstory, secret, unknown fact |
| **Conflict** | Disagreement, tension, callout |
| **Quotable** | A line that could stand alone as a tweet |
| **Story peak** | Narrative climax, payoff |
| **Practical value** | Actionable advice viewers can use |

Avoid: calm exposition, throat-clearing, recap, host monologue without a hook, technical jargon dumps.

Pick the top N (from `project.json`'s `n_clips`) non-overlapping windows that ALSO honor `exclude_windows`. Aim for variety across dimensions — don't pick 5 "emotional peak" clips.

Overwrite `projects/<slug>/2_picks.json` with the same schema:

```json
[
  {"i": 1, "start": 479.9, "end": 611.4, "duration": 131.5,
   "text_preview": "...", "score": 7.37}
]
```

Sort by `start`; index from 1.

If user passed `--review-picks`, stop and let them edit before continuing.

### 6. Translate **and verify** (only if LANG != "en")

ASR (whisper-turbo on Estonian) makes three recurring mistakes you must fix here — not translate blindly: **misheard names/loanwords**, **dropped or duplicated words**, and garbles that become **awkward English**. So treat the Estonian transcript as a noisy draft, not ground truth.

**6a. Build a glossary first.** Skim `1_transcript.json` for recurring proper nouns, guest/host names, brands, and foreign/loanwords. Cross-check spellings against `project.json` `title` (it holds the guest name) and real-world knowledge. Note the *correct* spellings (e.g. transcript "Stenniga"/"Stenn" → **Sten**; "laivi" → **live**; "Isha Free Offering" stays). If a name/term is clearly garbled and recurring, also append its correct spelling to `project.json` `terms` (array) — a future re-transcribe then seeds it into whisper's `initial_prompt` and never mishears it again.

**6b. For each pick:**
1. Slice the transcript segments where `s["end"] > clip_start AND s["start"] < clip_end`.
2. **Restore meaning before translating.** Read the Estonian in context; mentally repair obvious ASR damage (dropped words, a mangled name, a loanword spelled phonetically). Translate the *intended* sentence, never a literal garble.
3. Group into ~2-3 s chunks, breaking on punctuation. One readable English line per cue, max ~10 words. Don't merge across long pauses.
4. Write tight, natural English a native speaker would actually say. Match the speaker's rhythm — short Estonian = short English. No padding, no over-literal calques. Apply the 6a glossary for every name/term.

**6c. Verify pass — re-read each clip's cues end-to-end before saving:**
- Does it read as fluent English, or does any line betray a literal/garbled origin? Rewrite if so.
- Is every name/term spelled per the glossary?
- Any dropped words (a cue that doesn't parse) or duplicated phrases (ASR stutter)? Fix against the transcript.

Write `projects/<slug>/2_translations.json`:

```json
{
  "1": {"cues": [{"start": 0.0, "end": 2.5, "text": "..."}, ...]},
  "2": {"cues": [...]},
  ...
}
```

`start`/`end` are RELATIVE to the clip (0 = clip start).

If user passed `--review-translations`, stop here.

### 7-12. Finish — deterministic, long, run in background

```bash
python scripts/run_clip.py finish <slug>
```

Chains: `translate → cut → reframe → subtitle → burn --no-grade → cover`. ~25-30 min for N=5.

While it runs you can prep step 13's captions by reading `2_picks.json` + `2_translations.json` — the per-clip subtitle JSONs in `5_subs/clip_NN.json` only get written by the `translate` stage, so wait for that to land if you need them.

### 13. **Claude writes IG captions**

Read `projects/<slug>/5_subs/clip_NN.json` for each finished reel — these have the final EN cues. Also read `project.json` to get the `language` field for the IG post (the post-language is the user's IG audience language, which is `language` not `sub_language`).

Write one caption per reel to `projects/<slug>/7_captions.json`:

```json
{
  "1": {
    "text": "Caption body here.",
    "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]
  },
  "2": { ... }
}
```

Caption guidelines:
- Open with a hook — a question, a bold statement, or the most quotable line from the clip
- Reference the clip's specific idea, not a generic tease
- Write in the IG audience's language (the podcast's spoken language by default)
- 2-3 short lines max
- 5-8 hashtags: mix podcast-specific, topic-specific, and 1-2 broad-reach tags

### 14. Report to user

Concise table:

| Reel | Source window | Topic (3-5 words) | Size |
|---|---|---|---|
| 1 | MM:SS-MM:SS | ... | XX MB |

Mention covers at `projects/<slug>/7_covers/` and captions at `7_captions.json`.

## Resumability

`/clip <same path>` re-runs cheaply — every stage skips if its output exists. To force a re-pick, delete `2_picks.json`. To force a fresh slug, delete the project folder.

## Failure modes — handle without asking

- **Bash classifier rejects `/Volumes/...` paths.** Write a tiny `_setup.py` with `os.symlink`, run it, delete it. This has happened before; do it without escalating.
- **macOS TCC blocks reading an external-volume source** (`Operation not permitted` on read, *even with the sandbox disabled* — distinct from the classifier above, and no symlink fixes it). The host app (e.g. Cursor) lacks privacy permission for the removable volume. You **cannot** grant TCC from the shell, and `launchctl`-style attribution tricks are (correctly) blocked. Don't make the user grant Full Disk Access + restart the app — that ends the session. Instead drive **Finder** (it already has volume access) to copy the file onto the internal disk, then re-point the symlink. A long AppleEvent timeout is needed for big files, and the user must click the one-time "Cursor wants to control Finder" prompt:
  ```bash
  osascript -e 'with timeout of 3600 seconds
    tell application "Finder"
      duplicate (POSIX file "/Volumes/.../src.mp4" as alias) to (POSIX file "/abs/projects/<slug>/" as alias) with replacing
    end tell
  end timeout'
  # then: mv the copy to local.mp4, rm + relink source.mp4 -> local.mp4, verify with ffmpeg
  ```
  Delete the local copy after the reels are done.
- **tqdm progress bars overwriting log via `\r`.** Read with `LC_ALL=C tail -c N path | LC_ALL=C tr '\r' '\n' | tail -K`.
- **select_clips returns fewer than N picks.** Heuristic rejected weak candidates. Loosen by re-picking from `2_candidates.json` in step 5 — go down the ranked list until you have N strong ones.
- **Reframe drifts off-face / wrong speaker.** The tracker is hybrid YuNet (whole-frame detect) + MediaPipe (confirm-face only), HARD-CUTS between speakers (no panning), and scores the active speaker by **mouth-region pixel motion** (audio-gated) — *not* `jawOpen`, which reads flat-zero on small, side-facing couch faces (that bug parked the camera on the listener). **Diagnose before tuning:** a wrong-speaker result is usually a dead *signal*, not bad thresholds. Inspect `4_clips_reframed/clip_NN.track.json` — if a clip has 0-1 segments or sits ~100% on one `cx` side while that person isn't talking, the speaker signal failed; re-running `_detect_tracks` and printing per-track mouth-motion confirms it. Only tune `SPEAKER_STICKY_RATIO` / `MIN_DWELL_SEC` / `MIN_CUT_SEC` once you've confirmed the signal *does* separate the speakers (~2× motion). `--only clip_NN --debug` writes an annotated overlay (red = faces, green = active speaker, yellow = crop).

## Tone

Keep it concise — short, plain-language summaries over walls of text. Bullets and tables, not paragraphs. No emoji decoration. No "let me know if..." closers.

## What to NOT do

- Don't run any color grade. The user explicitly wants `--no-grade`.
- Don't `git add` or commit anything when done.
- Don't add `anthropic` or other LLM SDK deps — Claude is the LLM, in-conversation.
- Don't change subtitle style — `classic` is the user's confirmed default.
