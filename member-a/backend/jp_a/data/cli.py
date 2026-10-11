"""Generate A's synthetic data set.

    python -m jp_a.data.cli --out <data_dir> [--seed 42]

The default seed is ``A_SEED`` (settings.a_seed, 42). Output is byte-identical for the same seed.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ..config import get_settings
from .generator import write_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jp_a.data.cli", description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="data directory to write (e.g. member-a/data)")
    parser.add_argument("--seed", type=int, default=None, help="generator seed (default: A_SEED, 42)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    seed = args.seed if args.seed is not None else get_settings().a_seed
    if seed < 0:
        print("error: --seed must be a non-negative integer", file=sys.stderr)
        return 2
    written = write_files(args.out, seed)
    for relpath, path in written.items():
        print(f"wrote {relpath} ({path.stat().st_size} bytes)")
    print(f"seed {seed}: {len(written)} files under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
