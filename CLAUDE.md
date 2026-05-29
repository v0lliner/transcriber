# Transcriber — Podcast-to-Reels Pipeline

End-to-end automated pipeline that turns a long-form **16:9 podcast MP4** into **4-6 vertical 9:16 reels** with karaoke-style burned-in English subtitles, ready for Instagram / TikTok / Shorts.

Personal project (not tied to any business). Self-contained — no external services or paid APIs required.

## Project layout

Each podcast is one folder under `projects/`, with files numbered by pipeline stage so the layout self-documents.

```
transcriber/
├── CLAUDE.md
├── README.md
├── pipeline.py                       # CLI orchestrator
├── requirements.txt
├── venv/                             # gitignored
├── models/                           # shared ML model files (YuNet, etc.)
├── src/                              # reusable pipeline stages
│   ├── project.py                    # path helper — used by every stage
│   ├── transcribe.py
│   ├── select_clips.py
│   ├── translate.py
│   ├── cut.py
│   ├── reframe.py
│   ├── subtitle.py
│   └── burn.py
└── projects/
    └── <project_name>/
        ├── source.mp4                # symlink to original podcast
        ├── project.json              # metadata
        ├── 1_transcript.json         # whisper (word-level)
        ├── 1_audio.wav               # extracted mono 16 kHz
        ├── 2_picks.json              # selected clip windows (HUMAN-EDITED)
        ├── 2_chunks_et.json          # Estonian sub chunks (intermediate)
        ├── 2_translations.json       # English cues (HUMAN/LLM AUTHORED)
        ├── 2_candidates.json         # heuristic full candidate ranking
        ├── 3_clips_raw/              # 16:9 cuts
        ├── 4_clips_reframed/         # 9:16 face-tracked + denoised + lanczos
        ├── 5_subs/                   # ASS files + per-clip JSON
        ├── 6_reels/                  # FINAL videos (color graded + subs burned)
        └── 7_covers/ + 7_captions.json   # reserved for IG posting stage
```

## Tech stack

| Stage         | Tool                                                        |
|---------------|-------------------------------------------------------------|
| ASR           | `mlx-whisper` (Apple Silicon) — `large-v3-turbo`            |
| Face detect   | OpenCV `FaceDetectorYN` (YuNet ONNX, ~250 KB) — whole-frame  |
| Active speaker| Mouth-region pixel motion per face-crop, audio-gated         |
| Denoise+scale | ffmpeg `hqdn3d` (source res) → `scale=...:flags=lanczos`    |
| Color grade   | ffmpeg `eq + colorbalance + unsharp`                        |
| Subtitles     | ASS karaoke (V4+) — 6 styles, `classic` is default          |
| Cut / burn    | ffmpeg 7.x (H.264 high@4.0, AAC, yuv420p, faststart)        |

## Reframing logic

**Hybrid detector + hard jump-cuts** (no panning):

- 3.2× zoom from 1920×1080 source → 1080×1920 output
- Face center sits at **30% from top** (`FACE_Y_FRAC = 0.30`) — middle band clear for subtitles, body/gesture visible below
- **YuNet** detects + tracks faces across the whole frame (it catches the small couch-distance faces MediaPipe's selfie-tuned detector misses). **MediaPipe** runs on each face *crop* only to confirm it's a real face — a crop with no MediaPipe face = hand/false-positive → rejected. (`jawOpen` blendshapes are NOT used for speaker scoring: they read ~0 with no variance on the small, side-facing couch faces here.)
- Active speaker = track with the highest **mouth-region pixel motion** (mean frame-to-frame change in the lower-face patch) over a ±0.5 s window, gated by audio RMS (silence holds the current speaker). **No face-size prior** — a box-size tiebreaker used to park the camera on the bigger/closer face (the listener) whenever the mouth signal was weak. Hysteresis via `SPEAKER_STICKY_RATIO` + `MIN_DWELL_SEC`.
- **Jump-cuts, not panning:** consecutive samples are grouped into per-speaker segments; each segment gets ONE fixed crop center (median face position) and the camera cuts instantly at boundaries. Sub-`MIN_CUT_SEC` segments are merged away to avoid flash cuts.
- `python -m src.reframe --project <p> --only clip_NN --debug` writes an annotated overlay (red faces, green active speaker, yellow crop) for diagnosing tracking.

---

# Runbook: process a new podcast in 7 steps

This is the exact flow to follow in a fresh chat. Each stage prints what it did, and each stage can be re-run on its own. Files in `projects/<name>/` are the source of truth between stages — feel free to edit them between runs.

## Prereqs (first time only)

```bash
# Apple Silicon Mac, Python 3.12, ffmpeg 7.x on PATH
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# models are committed in models/ — no download needed
```

## Step 1 — Create the project folder

```bash
PROJECT=new_podcast       # use snake_case
mkdir -p projects/$PROJECT
ln -s "/absolute/path/to/source.mp4" projects/$PROJECT/source.mp4

cat > projects/$PROJECT/project.json <<EOF
{
  "name": "$PROJECT",
  "title": "Pretty title for credits",
  "language": "et",
  "sub_language": "en"
}
EOF
```

## Step 2 — Transcribe (≈ 30-60 min for 2 h podcast on M2 Air)

```bash
source venv/bin/activate
python -m src.transcribe --project $PROJECT
```
Writes `1_transcript.json` (word-level) and `1_audio.wav`.

## Step 3 — Pick clips (human-in-the-loop)

```bash
python -m src.select_clips --project $PROJECT
```
- Writes `2_candidates.json` (top 50 ranked) and a default `2_picks.json` (best 5 non-overlapping).
- **Open `2_picks.json` and edit it.** Keep 4-6 entries with `i`, `start`, `end`, `duration`, `hook`. Snap `start`/`end` to natural sentence boundaries; check against the transcript.
- To regenerate the default picks, pass `--force`.

## Step 4 — Translate (human-in-the-loop)

Ask the LLM in this chat to translate the chosen windows.
- Build the translation source: chunk the Estonian transcript inside each pick into ~2.5 s phrases. The translator (you or an LLM) writes `2_translations.json` shaped like:

```json
{
  "1": {
    "cues": [
      {"start": 0.0, "end": 2.5, "text": "English phrase here."},
      ...
    ]
  },
  "2": { ... }
}
```
`start`/`end` are RELATIVE to the clip (0 = clip start).

Then:
```bash
python -m src.translate --project $PROJECT
```
Writes `5_subs/clip_NN.json` per clip.

## Step 5 — Cut raw clips

```bash
python -m src.cut --project $PROJECT
```
Writes 16:9 cuts to `3_clips_raw/`. Fast (~10 s per clip).

## Step 6 — Reframe to 9:16 with face tracking (≈ 5 min per clip)

```bash
python -m src.reframe --project $PROJECT
```
Writes 1080×1920 tracked clips to `4_clips_reframed/`.
Tuning knobs in [src/reframe.py](src/reframe.py):
- `ZOOM` (default 3.2) — zoom factor
- `FACE_Y_FRAC` (default 0.30) — face position from top
- `SPEAKER_WINDOW_SEC` (default 0.5) — window for mouth-motion speaker scoring
- `SPEAKER_STICKY_RATIO` (default 1.8) — how much challenger must beat current to cut
- `MIN_DWELL_SEC` (default 1.5) — min hold on a speaker before a cut is allowed
- `MIN_CUT_SEC` (default 1.0) — segments shorter than this are merged (no flash cuts)
- Add `--only clip_NN` to reframe a single clip; `--debug` for the tracking overlay.

## Step 7 — Subtitles + burn

```bash
python -m src.subtitle --project $PROJECT --style classic
python -m src.burn     --project $PROJECT
```
- `--style classic` is the winner; pass `--style cycle` to rotate all 6 styles across clips (lets you A/B compare in one batch).
- `--accent` sets the karaoke highlight color (inactive words stay white). Default `cycle` rotates yellow→cyan→green→orange→pink→blue per clip so a batch isn't monochrome; pass a fixed name (e.g. `--accent yellow`) to pin one. Palette + per-clip logic in [src/subtitle.py](src/subtitle.py).
- Color grade is in [src/burn.py](src/burn.py) — variant A (mild contrast/sat + warm shift, light unsharp).
- Final videos land in `6_reels/`.

## All at once

```bash
python pipeline.py $PROJECT
# Skip stages you don't need to redo:
python pipeline.py $PROJECT --skip-transcribe --skip-cut
```

## Verification checklist before posting

- `ffprobe projects/$PROJECT/6_reels/reel_01.mp4` — should show `h264 / aac / 1080x1920 / yuv420p`
- Open each reel in QuickTime — face should be in upper third, subs in lower-middle, no jitter
- If a clip cuts mid-sentence, edit `2_picks.json` and rerun `cut → reframe → burn` (skip transcribe/select/translate)
- If subs look wrong, edit `2_translations.json`, rerun `translate → subtitle → burn`

## Committing changes

```bash
git add -A
git commit -m "Add <project_name> picks + translations"
git push
```

Heavy artifacts (mp4s, transcripts, reframed clips, reels) are gitignored — only project.json, picks, translations, and per-clip subtitle JSONs are committed (the editorial layer).

## Adding new subtitle styles

Edit [src/subtitle.py](src/subtitle.py) — each style is a function that takes cues and returns ASS body. Add to the `STYLES` list. Then `--style your_new_name`.

## Performance ballpark on M2 Air 8 GB

| Stage | Wall clock for 2 h podcast → 5 clips |
|---|---|
| transcribe | 30-60 min |
| select_clips | <1 s |
| translate | as fast as you can type the EN cues |
| cut | ~1 min total |
| reframe | ~25 min (5× clips × ~5 min each) |
| subtitle | <1 s |
| burn | ~5 min total |
| **TOTAL** | **~1.5 h** (most of it transcribe + reframe) |
