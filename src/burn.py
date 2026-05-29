"""Burn ASS subtitles + color grade onto reframed clips → final reels."""
import argparse
import subprocess

from src.project import Project

# Color grade "variant A" (mild) + light sharpen.
# Denoise is applied earlier in the reframe step at source resolution.
COLOR_GRADE = (
    "eq=contrast=1.10:saturation=1.30:gamma=0.97,"
    "colorbalance=rs=0.04:gs=0.01:bs=-0.06,"
    "unsharp=5:5:0.6:5:5:0.0"
)


def burn(in_clip, ass, out_clip, grade: bool = True) -> None:
    ass_abs = ass.resolve()
    vf = f"{COLOR_GRADE},ass={ass_abs}" if grade else f"ass={ass_abs}"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_clip),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-profile:v", "high", "-level", "4.0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_clip),
    ]
    print(f"[burn] {in_clip.name} + {ass.name} → {out_clip.name}")
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="my_podcast")
    ap.add_argument("--no-grade", action="store_true",
                    help="skip color grade / sharpen — output untouched colors")
    args = ap.parse_args()

    proj = Project(args.project)
    clips = sorted(proj.clips_reframed.glob("clip_*.mp4"))

    for clip in clips:
        ass = proj.subs / (clip.stem + ".ass")
        if not ass.exists():
            print(f"[burn] no subs for {clip.name}, skipping")
            continue
        out = proj.reels / ("reel_" + clip.stem.split("_")[-1] + ".mp4")
        if out.exists():
            print(f"[burn] skip existing {out.name}")
            continue
        burn(clip, ass, out, grade=not args.no_grade)

    print(f"[burn] done → {proj.reels}")


if __name__ == "__main__":
    main()
