"""Generate ASS karaoke subtitles for each clip, varying the visual style per clip.

Input: data/subs/clip_<NN>.json — { "i": NN, "duration": s, "cues": [{start, end, words: [...]}] }
       (where `start`/`end` are RELATIVE to the clip, not the source podcast)

Output: data/subs/clip_<NN>.ass

Six distinct styles are cycled across clips so you can pick your favourite.
"""
import argparse
import json
import re
from pathlib import Path
from typing import List

from src.project import Project

CHUNK_SIZE = 3   # words per on-screen group (overridable per style)

# ── ASS V4+ style helpers ────────────────────────────────────────────────────
# Colors are &H{AA}{BB}{GG}{RR}. Alpha 00 = opaque.
W = "&H00FFFFFF"      # white
Y = "&H0000FFFF"      # yellow (BGR -> 00FFFF means pure yellow)
GREEN = "&H0000FF00"
RED = "&H000000FF"
CYAN = "&H00FFFF00"
ORANGE = "&H000080FF"
BLACK = "&H00000000"

# Per-clip highlight accents — the karaoke "current word" color rotates through
# this palette across clips so a batch isn't monochrome yellow. Inline BGR form
# &HBBGGRR&. Applied by the karaoke styles (classic / top_bold / bouncy); the
# inactive words stay white.
HI_YELLOW = "&H00F0FF&"   # the original look (gold-yellow)
HI_CYAN = "&HFFFF00&"
HI_GREEN = "&H50FF50&"
HI_ORANGE = "&H00A0FF&"
HI_PINK = "&HC840FF&"
HI_BLUE = "&HFFB440&"
HIGHLIGHT_PALETTE = [HI_YELLOW, HI_CYAN, HI_GREEN, HI_ORANGE, HI_PINK, HI_BLUE]
DEFAULT_ACCENT = HI_YELLOW


def fmt_ts(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    cs = int(round(s * 100))
    si = cs // 100
    csi = cs % 100
    return f"{h:01d}:{m:02d}:{si:02d}.{csi:02d}"


def split_words(text: str):
    return [w for w in re.split(r"\s+", text.strip()) if w]


# ── Six distinct styles ──────────────────────────────────────────────────────
# Each style is a function that, given the list of cues, returns the full
# ASS file body as a string.
PLAY_W, PLAY_H = 1080, 1920


def _header(style_line: str) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {PLAY_W}\n"
        f"PlayResY: {PLAY_H}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 0\n"
        "YCbCr Matrix: TV.709\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style_line}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


DIALOGUE = "Dialogue: 0,{start},{end},Base,,0,0,0,,{text}"


def _word_times(words, t_start, t_end):
    dur = max(t_end - t_start, 0.001)
    per = dur / len(words)
    return [(w, t_start + i * per, t_start + (i + 1) * per) for i, w in enumerate(words)]


# Style 1 — Classic karaoke (matches the archived "klipp 2" look)
def style_classic(cues, accent=DEFAULT_ACCENT) -> str:
    style = (
        "Style: Base,Arial Black,96," + W + "," + W + "," + BLACK + ",&H64000000,"
        "-1,0,0,0,100,100,0,0,1,7,3,2,80,80,900,1"
    )
    out = [_header(style)]
    for cue in cues:
        words = split_words(cue["text"])
        if not words:
            continue
        wt = _word_times(words, cue["start"], cue["end"])
        for ci in range(0, len(words), CHUNK_SIZE):
            chunk = wt[ci:ci + CHUNK_SIZE]
            chunk_only = [w for (w, _, _) in chunk]
            for k, (w, ws, we) in enumerate(chunk):
                parts = []
                for idx, ww in enumerate(chunk_only):
                    if idx == k:
                        parts.append(r"{\1c" + accent + r"\fscx118\fscy118\bord9}" + ww.upper() + r"{\r}")
                    else:
                        parts.append(r"{\1c&HFFFFFF&}" + ww.upper() + r"{\r}")
                out.append(DIALOGUE.format(start=fmt_ts(ws), end=fmt_ts(we), text=" ".join(parts)))
    return "\n".join(out) + "\n"


# Style 2 — "Hype" multicolor (Mr Beast inspired): big Impact, each word a different bold color
def style_hype(cues, accent=DEFAULT_ACCENT) -> str:
    style = (
        "Style: Base,Impact,108," + W + "," + W + "," + BLACK + ",&H80000000,"
        "-1,0,0,0,100,100,0,0,1,8,4,2,80,80,850,1"
    )
    palette = [W, Y, GREEN, ORANGE, CYAN]
    out = [_header(style)]
    color_idx = 0
    for cue in cues:
        words = split_words(cue["text"])
        if not words:
            continue
        wt = _word_times(words, cue["start"], cue["end"])
        for ci in range(0, len(words), 2):
            chunk = wt[ci:ci + 2]
            chunk_only = [w for (w, _, _) in chunk]
            for k, (w, ws, we) in enumerate(chunk):
                parts = []
                for idx, ww in enumerate(chunk_only):
                    c = palette[(color_idx + idx) % len(palette)]
                    if idx == k:
                        parts.append(r"{\1c" + c + r"&\fscx128\fscy128\bord10\shad6}" + ww.upper() + r"{\r}")
                    else:
                        parts.append(r"{\1c" + c + r"&\fscx95\fscy95\alpha&H50&}" + ww.upper() + r"{\r}")
                out.append(DIALOGUE.format(start=fmt_ts(ws), end=fmt_ts(we), text=" ".join(parts)))
            color_idx += 1
    return "\n".join(out) + "\n"


# Style 3 — Minimal box: lowercase, sentence-at-a-time, semi-transparent black box
def style_minimal_box(cues, accent=DEFAULT_ACCENT) -> str:
    # BorderStyle 3 = opaque box using OutlineColour as box bg
    style = (
        "Style: Base,Helvetica,70," + W + "," + W + ",&HC0000000,&HC0000000,"
        "0,0,0,0,100,100,0,0,3,18,0,2,80,80,400,1"
    )
    out = [_header(style)]
    for cue in cues:
        text = cue["text"].strip()
        if not text:
            continue
        out.append(DIALOGUE.format(
            start=fmt_ts(cue["start"]),
            end=fmt_ts(cue["end"]),
            text=text.lower(),
        ))
    return "\n".join(out) + "\n"


# Style 4 — Top bold with karaoke highlight (accent rotates per clip)
def style_top_bold(cues, accent=DEFAULT_ACCENT) -> str:
    style = (
        "Style: Base,Arial Black,92," + Y + "," + Y + "," + BLACK + ",&H80000000,"
        "-1,0,0,0,100,100,0,0,1,6,3,8,80,80,250,1"
    )
    out = [_header(style)]
    for cue in cues:
        words = split_words(cue["text"])
        if not words:
            continue
        wt = _word_times(words, cue["start"], cue["end"])
        for ci in range(0, len(words), CHUNK_SIZE):
            chunk = wt[ci:ci + CHUNK_SIZE]
            chunk_only = [w for (w, _, _) in chunk]
            for k, (w, ws, we) in enumerate(chunk):
                parts = []
                for idx, ww in enumerate(chunk_only):
                    if idx == k:
                        parts.append(r"{\1c" + accent + r"\fscx120\fscy120}" + ww.upper() + r"{\r}")
                    else:
                        parts.append(r"{\1c&HFFFFFF&}" + ww.upper() + r"{\r}")
                out.append(DIALOGUE.format(start=fmt_ts(ws), end=fmt_ts(we), text=" ".join(parts)))
    return "\n".join(out) + "\n"


# Style 5 — Big single word, center-screen, white with strong outline
def style_solo_big(cues, accent=DEFAULT_ACCENT) -> str:
    style = (
        "Style: Base,Impact,170," + W + "," + W + "," + BLACK + ",&H00000000,"
        "-1,0,0,0,100,100,0,0,1,12,0,5,80,80,0,1"
    )
    out = [_header(style)]
    for cue in cues:
        words = split_words(cue["text"])
        if not words:
            continue
        wt = _word_times(words, cue["start"], cue["end"])
        for w, ws, we in wt:
            out.append(DIALOGUE.format(
                start=fmt_ts(ws),
                end=fmt_ts(we),
                text=r"{\1c&HFFFFFF&}" + w.upper(),
            ))
    return "\n".join(out) + "\n"


# Style 6 — Bouncy: chunk of 4 words, current word scales up + pops
def style_bouncy(cues, accent=DEFAULT_ACCENT) -> str:
    style = (
        "Style: Base,Avenir Next Heavy,86," + W + "," + W + "," + BLACK + ",&H80000000,"
        "-1,0,0,0,100,100,0,0,1,7,4,2,80,80,700,1"
    )
    out = [_header(style)]
    for cue in cues:
        words = split_words(cue["text"])
        if not words:
            continue
        wt = _word_times(words, cue["start"], cue["end"])
        for ci in range(0, len(words), 4):
            chunk = wt[ci:ci + 4]
            chunk_only = [w for (w, _, _) in chunk]
            for k, (w, ws, we) in enumerate(chunk):
                parts = []
                for idx, ww in enumerate(chunk_only):
                    if idx == k:
                        parts.append(
                            r"{\1c" + accent + r"\fscx140\fscy140\bord10\t(0,120,\fscx115\fscy115)}"
                            + ww.upper() + r"{\r}"
                        )
                    else:
                        parts.append(r"{\1c&HFFFFFF&\alpha&H30&}" + ww.upper() + r"{\r}")
                out.append(DIALOGUE.format(start=fmt_ts(ws), end=fmt_ts(we), text=" ".join(parts)))
    return "\n".join(out) + "\n"


STYLES = [
    ("classic",     style_classic),
    ("hype",        style_hype),
    ("minimal_box", style_minimal_box),
    ("top_bold",    style_top_bold),
    ("solo_big",    style_solo_big),
    ("bouncy",      style_bouncy),
]


ACCENT_NAMES = {
    HI_YELLOW: "yellow", HI_CYAN: "cyan", HI_GREEN: "green",
    HI_ORANGE: "orange", HI_PINK: "pink", HI_BLUE: "blue",
}


def build_for_clip(clip_json: Path, out_ass: Path, style_idx: int,
                   accent: str = DEFAULT_ACCENT) -> tuple:
    data = json.loads(clip_json.read_text(encoding="utf-8"))
    cues = data["cues"]
    name, fn = STYLES[style_idx % len(STYLES)]
    body = fn(cues, accent=accent)
    out_ass.write_text(body, encoding="utf-8")
    return name, ACCENT_NAMES.get(accent, accent)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="my_podcast")
    ap.add_argument("--style", default="classic",
                    help="force ONE style for every clip (default 'classic'). "
                         "Pass 'cycle' to rotate through all 6 styles.")
    ap.add_argument("--accent", default="cycle",
                    help="karaoke highlight color: 'cycle' (default — rotate the "
                         f"palette per clip) or one of {list(ACCENT_NAMES.values())}.")
    args = ap.parse_args()

    proj = Project(args.project)
    style_names = [name for (name, _fn) in STYLES]
    forced_idx = None
    if args.style != "cycle":
        if args.style not in style_names:
            raise SystemExit(f"unknown style '{args.style}'. Available: {style_names + ['cycle']}")
        forced_idx = style_names.index(args.style)

    name_to_accent = {v: k for k, v in ACCENT_NAMES.items()}
    forced_accent = None
    if args.accent != "cycle":
        if args.accent not in name_to_accent:
            raise SystemExit(f"unknown accent '{args.accent}'. Available: "
                             f"{list(name_to_accent) + ['cycle']}")
        forced_accent = name_to_accent[args.accent]

    clips = sorted(proj.subs.glob("clip_*.json"))
    if not clips:
        print(f"[subtitle] no clip JSON in {proj.subs}")
        return
    manifest = []
    for i, cj in enumerate(clips):
        out = cj.with_suffix(".ass")
        style_idx = forced_idx if forced_idx is not None else i
        accent = forced_accent if forced_accent is not None \
            else HIGHLIGHT_PALETTE[i % len(HIGHLIGHT_PALETTE)]
        name, accent_name = build_for_clip(cj, out, style_idx, accent)
        manifest.append({"clip": cj.stem, "style": name,
                         "accent": accent_name, "ass": str(out)})
        print(f"[subtitle] {cj.stem} → style '{name}' / accent '{accent_name}' → {out.name}")
    (proj.subs / "style_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"[subtitle] wrote style_manifest.json")


if __name__ == "__main__":
    main()
