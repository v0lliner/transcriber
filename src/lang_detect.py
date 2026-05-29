"""Detect the language of a project's transcript.

Prints an ISO 639-1 code to stdout (one line). Also updates project.json's
`language` field in place so downstream stages can read it.

Strategy:
  1. Trust mlx-whisper's `language` metadata at the transcript root (most reliable).
  2. Fallback: count high-frequency word hits for ET vs EN in the first 50 segments.
"""
import argparse
import json
import re
import sys

from src.project import Project


EN_WORDS = {
    "the", "and", "is", "to", "of", "a", "in", "that", "it", "for",
    "was", "with", "as", "on", "be", "at", "this", "have", "i", "you",
    "but", "not", "they", "we", "are", "so", "or", "if",
}
ET_WORDS = {
    "ja", "on", "et", "see", "ma", "sa", "ka", "kui", "siis", "no",
    "aga", "ei", "olen", "oli", "tema", "mis", "mida", "kõik", "väga",
    "nüüd", "noh", "üks", "kaks", "kus", "kuidas",
}

WORD_RE = re.compile(r"[a-zA-ZäöüõšžÄÖÜÕŠŽ]+")
LANG_MAP = {  # normalize verbose names to ISO
    "estonian": "et", "english": "en", "russian": "ru", "finnish": "fi",
    "german": "de", "spanish": "es", "french": "fr",
}


def normalize(code: str) -> str:
    code = (code or "").strip().lower()
    return LANG_MAP.get(code, code[:2] if code else "")


def detect(transcript: dict) -> str:
    meta = normalize(transcript.get("language", ""))
    if meta:
        return meta
    segs = transcript.get("segments", [])[:50]
    text = " ".join(s.get("text", "") for s in segs).lower()
    tokens = WORD_RE.findall(text)
    et_hits = sum(1 for t in tokens if t in ET_WORDS)
    en_hits = sum(1 for t in tokens if t in EN_WORDS)
    return "et" if et_hits >= en_hits else "en"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    args = ap.parse_args()

    proj = Project(args.project)
    if not proj.transcript.exists():
        sys.exit(f"missing transcript: {proj.transcript}")
    tx = json.loads(proj.transcript.read_text(encoding="utf-8"))
    lang = detect(tx)

    meta = proj.load_meta()
    meta["language"] = lang
    proj.save_meta(meta)

    print(lang)


if __name__ == "__main__":
    main()
