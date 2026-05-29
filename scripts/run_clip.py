"""Orchestrator for the /clip skill.

Subcommands:
  bootstrap PATH [--exclude WINDOWS] [--n N]
      Slugify the source filename, create projects/<slug>/, symlink the source
      MP4 to projects/<slug>/source.mp4, and write a project.json populated with
      `n_clips` + optional `exclude_windows`. Prints `SLUG=<slug>` so the caller
      can capture the slug.

  finish SLUG
      Chains the deterministic post-translation stages:
      translate -> cut -> reframe -> subtitle (classic) -> burn (--no-grade) -> cover.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


SKIP_TOKENS = re.compile(r"^(podcast|the|episode|ep|pod)$", re.IGNORECASE)


def _slugify(filename: str) -> str:
    name = Path(filename).stem
    name = re.sub(r"#\d+", " ", name)               # drop episode markers
    name = re.sub(r"[^A-Za-z0-9äöüõÄÖÜÕ]+", " ", name).strip()
    tokens = [t.lower() for t in name.split() if not SKIP_TOKENS.match(t)]
    table = str.maketrans({
        "ü": "y", "Ü": "y",
        "ä": "a", "Ä": "a",
        "ö": "o", "Ö": "o",
        "õ": "o", "Õ": "o",
    })
    slug = "_".join(tokens).translate(table)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "podcast"


def _to_sec(t: str):
    t = t.strip()
    if t.lower() == "end":
        return None
    if ":" in t:
        m, s = t.split(":")
        return int(m) * 60 + int(s)
    return int(t)


def _parse_excl(s: str) -> list:
    if not s:
        return []
    out = []
    for piece in s.split(","):
        piece = piece.strip()
        if not piece:
            continue
        a, b = piece.split("-")
        out.append([_to_sec(a), _to_sec(b)])
    return out


def cmd_bootstrap(args) -> None:
    src = Path(args.path).expanduser()
    if not src.exists():
        sys.exit(f"missing source: {src}")
    slug = _slugify(src.name)
    proj_dir = Path("projects") / slug
    proj_dir.mkdir(parents=True, exist_ok=True)

    link = proj_dir / "source.mp4"
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(str(src), str(link))

    meta = {
        "name": slug,
        "title": src.stem,
        "source": str(src),
        "language": "et",            # overwritten after lang_detect
        "sub_language": "en",
        "n_clips": int(args.n),
        "ig_account": "podcast",
    }
    excluded = _parse_excl(args.exclude)
    if excluded:
        meta["exclude_windows"] = excluded
    (proj_dir / "project.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[bootstrap] {src.name} → projects/{slug}/")
    print(f"SLUG={slug}")


def _run(module: str, slug: str, *extra) -> None:
    cmd = [sys.executable, "-m", module, "--project", slug, *extra]
    print(f"\n$ {' '.join(cmd)}")
    if subprocess.call(cmd) != 0:
        sys.exit(f"!! {module} failed")


def cmd_finish(args) -> None:
    slug = args.slug
    _run("src.translate", slug)
    _run("src.cut", slug)
    _run("src.reframe", slug)
    _run("src.subtitle", slug, "--style", "classic")
    _run("src.burn", slug, "--no-grade")
    _run("src.cover", slug)
    print(f"\n✓ /clip done → projects/{slug}/6_reels/  +  /7_covers/")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    boot = sub.add_parser("bootstrap")
    boot.add_argument("path", help="path to source .mp4")
    boot.add_argument("--exclude", default="",
                      help='comma-separated MM:SS-MM:SS windows to skip (e.g. "13:57-16:56,42:51-end")')
    boot.add_argument("--n", default=5, help="target number of clips (default 5)")

    fin = sub.add_parser("finish")
    fin.add_argument("slug")

    args = ap.parse_args()
    if args.cmd == "bootstrap":
        cmd_bootstrap(args)
    elif args.cmd == "finish":
        cmd_finish(args)


if __name__ == "__main__":
    main()
