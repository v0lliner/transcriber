"""End-to-end orchestrator: source.mp4 → 4-6 vertical reels with EN karaoke subs.

Usage:
  python pipeline.py my_podcast                 # full pipeline
  python pipeline.py my_podcast --skip-transcribe --skip-cut

Stages can be skipped individually so you can iterate on later stages without
re-running expensive earlier ones.
"""
import argparse
import subprocess
import sys


STAGES = [
    ("transcribe", "src.transcribe"),
    ("select",     "src.select_clips"),
    ("translate",  "src.translate"),
    ("cut",        "src.cut"),
    ("reframe",    "src.reframe"),
    ("subtitle",   "src.subtitle"),
    ("burn",       "src.burn"),
]


def run(module: str, project: str) -> None:
    cmd = [sys.executable, "-m", module, "--project", project]
    print(f"\n$ {' '.join(cmd)}")
    p = subprocess.run(cmd)
    if p.returncode != 0:
        print(f"!! stage failed: {module}", file=sys.stderr)
        sys.exit(p.returncode)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="project name under projects/")
    for name, _ in STAGES:
        ap.add_argument(f"--skip-{name}", action="store_true")
    args = ap.parse_args()

    for name, module in STAGES:
        if getattr(args, f"skip_{name.replace('-', '_')}"):
            continue
        run(module, args.project)

    print(f"\n✓ pipeline complete → projects/{args.project}/6_reels/")


if __name__ == "__main__":
    main()
