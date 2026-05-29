"""Heuristic clip picker — generates candidate windows + default picks.

Reads the project's `1_transcript.json`, writes:
  2_candidates.json — full ranked list
  2_picks.json      — 4-6 best windows (only if missing, unless --force)

In practice the picks are usually hand-curated. Run this once to seed; then
edit `2_picks.json` directly to override.
"""
import argparse
import json
import re

from src.project import Project


MIN_DUR = 95.0     # 1:35
MAX_DUR = 175.0    # 2:55
TARGET = 5

INTERROGATIVE = re.compile(
    r"^\s*(kas|mis|miks|kuidas|millal|kus|kelle|kuhu)\b", re.IGNORECASE
)
HOOK_WORDS = re.compile(
    r"\b(kõige|alati|mitte kunagi|saladus|tegelikult|tähtsam|"
    r"uskumatu|šokk|hullem|parim|huvitav|naljakas)\b",
    re.IGNORECASE,
)


def is_sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?…]\s*$", (text or "").strip()))


def score(window_segs):
    text = " ".join(s.get("text", "") for s in window_segs).strip()
    dur = window_segs[-1]["end"] - window_segs[0]["start"]
    sent_density = sum(1 for s in window_segs if is_sentence_end(s.get("text", ""))) / max(dur, 1.0)
    opener = window_segs[0].get("text", "").strip()
    bonus = 0
    if INTERROGATIVE.match(opener):
        bonus += 2
    bonus += len(HOOK_WORDS.findall(text)) * 0.8
    dur_pref = 1.0 - abs(dur - 130) / 130
    return sent_density * 4 + bonus + dur_pref


def build_candidates(segments):
    out = []
    n = len(segments)
    for i, s in enumerate(segments):
        if i != 0 and not is_sentence_end(segments[i - 1]["text"]):
            continue
        for j in range(i + 1, n):
            dur = segments[j]["end"] - s["start"]
            if dur < MIN_DUR:
                continue
            if dur > MAX_DUR:
                break
            if not is_sentence_end(segments[j].get("text", "")):
                continue
            window = segments[i : j + 1]
            out.append({
                "start": s["start"],
                "end": segments[j]["end"],
                "duration": dur,
                "text_preview": " ".join(x["text"].strip() for x in window)[:240],
                "score": score(window),
            })
    out.sort(key=lambda x: -x["score"])
    return out


def _overlaps_excluded(c, excluded):
    """True if candidate c intersects any excluded [start, end] window.
    `end` may be None (= to end of podcast)."""
    for ex_start, ex_end in excluded:
        if ex_end is None:
            if c["end"] > ex_start:
                return True
        else:
            if c["start"] < ex_end and c["end"] > ex_start:
                return True
    return False


def deduplicate(candidates, target=TARGET, gap=10.0, excluded=None):
    excluded = excluded or []
    chosen = []
    for c in candidates:
        if excluded and _overlaps_excluded(c, excluded):
            continue
        if any(not (c["end"] + gap < k["start"] or c["start"] > k["end"] + gap) for k in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= target:
            break
    chosen.sort(key=lambda x: x["start"])
    for i, c in enumerate(chosen, start=1):
        c["i"] = i
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="my_podcast")
    ap.add_argument("--force", action="store_true",
                    help="overwrite 2_picks.json even if it exists")
    args = ap.parse_args()

    proj = Project(args.project)
    tx = json.loads(proj.transcript.read_text(encoding="utf-8"))
    segments = tx.get("segments", [])
    candidates = build_candidates(segments)
    proj.candidates.write_text(
        json.dumps(candidates[:50], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[select_clips] {len(candidates)} candidates, top 50 → {proj.candidates.name}")

    if proj.picks.exists() and not args.force:
        print(f"[select_clips] {proj.picks.name} already exists, leaving it alone")
        return
    meta = proj.load_meta()
    target = int(meta.get("n_clips", TARGET))
    excluded = meta.get("exclude_windows", [])
    if excluded:
        def fmt(t):
            if t is None: return "end"
            m, s = divmod(int(t), 60); return f"{m}:{s:02d}"
        zones = ", ".join(f"[{fmt(a)}-{fmt(b)}]" for a, b in excluded)
        print(f"[select_clips] excluding windows: {zones}")
    picks = deduplicate(candidates, target=target, excluded=excluded)
    proj.picks.write_text(json.dumps(picks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[select_clips] wrote {len(picks)} picks → {proj.picks.name}")


if __name__ == "__main__":
    main()
