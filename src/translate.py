"""Build per-clip subtitle JSON from picks + word-timed transcript + translations.

Translations are authored by hand (the human or an LLM in the same session)
and saved to `2_translations.json` with shape:

    {
      "1": {"cues": [{"start": 0.0, "end": 2.3, "text": "English sentence."}, …]},
      "2": { ... }
    }

`start`/`end` are RELATIVE to the clip (0 = clip start).

This step slices the picks from the transcript, looks up matching English cues,
and writes per-clip subtitle JSON the subtitle generator consumes.
"""
import argparse
import json

from src.project import Project


def slice_segments(segments, clip_start, clip_end):
    out = []
    for s in segments:
        if s["end"] <= clip_start:
            continue
        if s["start"] >= clip_end:
            break
        out.append(s)
    return out


# Readable-line targets for the Estonian fallback chunker.
MIN_CUE_SEC = 1.2
MAX_CUE_SEC = 3.0
MAX_CUE_WORDS = 9


def merge_cues(cues):
    """Glue tiny one-word fallback cues into readable ~1.2-3s lines.

    Merges a cue into the previous one while the result stays under the duration
    / word caps, but never merges across sentence-ending punctuation.
    """
    merged: list[dict] = []
    for c in cues:
        text = c["text"].strip()
        if not text:
            continue
        if merged:
            prev = merged[-1]
            prev_done = prev["text"].rstrip().endswith((".", "!", "?", "…"))
            dur = c["end"] - prev["start"]
            words = len((prev["text"] + " " + text).split())
            too_short = (prev["end"] - prev["start"]) < MIN_CUE_SEC
            fits = dur <= MAX_CUE_SEC and words <= MAX_CUE_WORDS
            if not prev_done and (too_short or fits):
                prev["text"] = (prev["text"].rstrip() + " " + text).strip()
                prev["end"] = c["end"]
                continue
        merged.append({"start": c["start"], "end": c["end"], "text": text})
    return merged


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="my_podcast")
    args = ap.parse_args()

    proj = Project(args.project)
    picks = json.loads(proj.picks.read_text(encoding="utf-8"))
    tx = json.loads(proj.transcript.read_text(encoding="utf-8"))
    tr = json.loads(proj.translations.read_text(encoding="utf-8")) if proj.translations.exists() else {}

    for p in picks:
        idx = p["i"]
        key = str(idx)
        clip_start, clip_end = p["start"], p["end"]
        et_segs = slice_segments(tx.get("segments", []), clip_start, clip_end)

        # Estonian-side cues with relative timings (fallback), merged into readable lines
        et_cues = merge_cues([{
            "start": max(0.0, s["start"] - clip_start),
            "end":   min(clip_end - clip_start, s["end"] - clip_start),
            "text":  s["text"].strip(),
        } for s in et_segs])

        clip_obj = {
            "i": idx, "start": clip_start, "end": clip_end,
            "duration": clip_end - clip_start, "cues": et_cues,
        }

        if key in tr and tr[key].get("cues"):
            clip_obj["cues"] = tr[key]["cues"]
            clip_obj["lang"] = "en"
        else:
            clip_obj["lang"] = "et"
            print(f"[translate] WARNING: no English for clip {idx}, using Estonian fallback")

        (proj.subs / f"clip_{idx:02d}.json").write_text(
            json.dumps(clip_obj, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"[translate] wrote {len(picks)} per-clip JSON → {proj.subs}/")


if __name__ == "__main__":
    main()
