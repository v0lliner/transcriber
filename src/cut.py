"""Cut raw 16:9 clips from the source MP4 based on `2_picks.json`."""
import argparse
import json
import subprocess

from src.project import Project


def cut(video, start: float, end: float, out) -> None:
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(video),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="my_podcast")
    args = ap.parse_args()

    proj = Project(args.project)
    picks = json.loads(proj.picks.read_text(encoding="utf-8"))

    for p in picks:
        idx = p["i"]
        out = proj.clips_raw / f"clip_{idx:02d}.mp4"
        if out.exists():
            print(f"[cut] skip existing {out.name}")
            continue
        print(f"[cut] clip_{idx:02d}: {p['start']:.2f}s → {p['end']:.2f}s ({p['end']-p['start']:.1f}s)")
        cut(proj.source, p["start"], p["end"], out)

    print(f"[cut] {len(picks)} clips → {proj.clips_raw}")


if __name__ == "__main__":
    main()
