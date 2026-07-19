from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.shadowbot_markdown_report import write_formal_boundary_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a human-readable Markdown report from a ShadowBot acceptance JSON artifact."
    )
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 acceptance JSON")
    parser.add_argument("--output", required=True, type=Path, help="UTF-8 Markdown report")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = write_formal_boundary_markdown(args.input, args.output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
