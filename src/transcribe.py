"""Transcribe a project's source MP4 to Estonian word-level JSON (mlx-whisper).

Accuracy aids (no speed cost — same turbo model):
  - `initial_prompt` built from project.json `title` + `terms` so whisper spells
    the guest's name and known proper nouns / loanwords correctly instead of
    guessing ("Sten Püvi", not "Stenniga").
  - `hallucination_silence_threshold` + temperature fallback to suppress the
    repeated / invented words whisper emits over silence and music.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

import mlx_whisper

from src.project import Project

MODEL = "mlx-community/whisper-large-v3-turbo"

# Temperature fallback: start greedy, escalate only when a chunk looks degenerate
# (high compression ratio / low logprob). Mirrors openai-whisper's default ladder.
TEMPERATURE_FALLBACK = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def extract_audio(video: Path, out_wav: Path) -> None:
    if out_wav.exists():
        return
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(out_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def build_initial_prompt(meta: dict) -> str | None:
    """Bias the decoder toward the right proper nouns.

    Pulls the guest name out of the title (dropping 'PODCAST #N' boilerplate) and
    appends any `terms` listed in project.json. Returned as a short Estonian-style
    context string; None if there's nothing useful to seed.
    """
    title = (meta.get("title") or "").strip()
    # Drop "PODCAST #12", "Podcast nr 3", "Saade #5" style prefixes — keep the name.
    name = re.sub(r"(?i)\b(podcast|saade|episood|episode)\b\s*(nr\.?|#)?\s*\d*", "", title)
    name = name.strip(" -–—:·.")

    parts: list[str] = []
    if name:
        parts.append(f"Külaline: {name}.")
    terms = [str(t).strip() for t in (meta.get("terms") or []) if str(t).strip()]
    if terms:
        parts.append("Mainitud nimed ja mõisted: " + ", ".join(terms) + ".")
    prompt = " ".join(parts).strip()
    return prompt or None


def transcribe(audio: Path, out_json: Path, language: str = "et",
               initial_prompt: str | None = None) -> dict:
    if initial_prompt:
        print(f"[transcribe] initial_prompt: {initial_prompt!r}")
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=MODEL,
        language=language,
        word_timestamps=True,
        initial_prompt=initial_prompt,
        temperature=TEMPERATURE_FALLBACK,
        condition_on_previous_text=True,        # keep context for grammar/coherence
        compression_ratio_threshold=2.4,        # flag repetition → triggers fallback
        logprob_threshold=-1.0,                 # flag low-confidence → triggers fallback
        no_speech_threshold=0.6,
        hallucination_silence_threshold=2.0,    # skip long silences that breed gibberish
        verbose=False,
    )
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="my_podcast")
    args = ap.parse_args()

    proj = Project(args.project)
    if not proj.source.exists():
        raise SystemExit(f"missing {proj.source} — point a symlink at the source MP4")

    meta = proj.load_meta()
    language = meta.get("language", "et")
    initial_prompt = build_initial_prompt(meta)

    print(f"[transcribe] extracting audio → {proj.audio}")
    extract_audio(proj.source, proj.audio)
    print(f"[transcribe] running {MODEL} (lang={language})")
    result = transcribe(proj.audio, proj.transcript, language, initial_prompt)
    print(f"[transcribe] wrote {proj.transcript} ({len(result.get('segments', []))} segments)")


if __name__ == "__main__":
    main()
