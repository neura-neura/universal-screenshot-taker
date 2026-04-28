from __future__ import annotations

import argparse

from screenshot_taker.legacy import run_legacy_cli
from screenshot_taker.ui import run_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Universal screenshot automation tool.")
    parser.add_argument(
        "--legacy-cli",
        action="store_true",
        help="Run the original console workflow for eLibro-compatible automation.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.legacy_cli:
        run_legacy_cli()
        return 0
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
