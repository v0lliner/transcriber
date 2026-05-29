"""Project paths.

Every podcast/episode is one folder under `projects/`, with files numbered by
pipeline stage so the layout self-documents:

    projects/
    └── my_podcast/
        ├── source.mp4              # symlink to the original podcast file
        ├── project.json            # metadata (lang, IG account, n_clips, …)
        ├── 1_transcript.json       # whisper output (word-level)
        ├── 1_audio.wav             # extracted mono 16 kHz (can be deleted)
        ├── 2_picks.json            # selected clip windows
        ├── 2_chunks_et.json        # Estonian sub chunks (intermediate)
        ├── 2_translations.json     # English translation cues
        ├── 2_candidates.json       # algorithm's full candidate list
        ├── 3_clips_raw/            # 16:9 cuts (lossless-ish)
        ├── 4_clips_reframed/       # 9:16 face-tracked + denoised + lanczos
        ├── 5_subs/                 # per-clip ASS files + clip_NN.json
        ├── 6_reels/                # FINAL videos (color graded + subs burned)
        ├── 7_covers/               # cover_NN.jpg per reel (chosen by cover.py)
        └── 7_captions.json         # per-reel IG caption text

Use `Project("my_podcast")` and access paths as attributes.
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECTS_ROOT = Path("projects")


class Project:
    """Lazy-creating wrapper around a single podcast project directory."""

    def __init__(self, name: str):
        self.name = name
        self.root = PROJECTS_ROOT / name
        self.root.mkdir(parents=True, exist_ok=True)

    # --- top-level files ---
    @property
    def source(self) -> Path:        return self.root / "source.mp4"
    @property
    def meta_file(self) -> Path:     return self.root / "project.json"

    # --- stage 1: transcribe ---
    @property
    def transcript(self) -> Path:    return self.root / "1_transcript.json"
    @property
    def audio(self) -> Path:         return self.root / "1_audio.wav"

    # --- stage 2: select + translate ---
    @property
    def picks(self) -> Path:         return self.root / "2_picks.json"
    @property
    def candidates(self) -> Path:    return self.root / "2_candidates.json"
    @property
    def windows(self) -> Path:       return self.root / "2_windows.json"
    @property
    def chunks_et(self) -> Path:     return self.root / "2_chunks_et.json"
    @property
    def translations(self) -> Path:  return self.root / "2_translations.json"

    # --- stage 3-7: directories (auto-create on access) ---
    def _dir(self, name: str) -> Path:
        d = self.root / name
        d.mkdir(exist_ok=True)
        return d

    @property
    def clips_raw(self) -> Path:       return self._dir("3_clips_raw")
    @property
    def clips_reframed(self) -> Path:  return self._dir("4_clips_reframed")
    @property
    def subs(self) -> Path:            return self._dir("5_subs")
    @property
    def reels(self) -> Path:           return self._dir("6_reels")
    @property
    def covers(self) -> Path:          return self._dir("7_covers")
    @property
    def captions(self) -> Path:        return self.root / "7_captions.json"

    # --- metadata helpers ---
    def load_meta(self) -> dict:
        if self.meta_file.exists():
            return json.loads(self.meta_file.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, data: dict) -> None:
        self.meta_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
