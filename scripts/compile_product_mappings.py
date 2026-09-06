from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.product_mapping import (  # noqa: E402
    compile_product_mapping_workbook,
    write_immutable_product_mapping,
)


DEFAULT_SOURCE = ROOT / "data" / "samples" / "platform_mappings.xlsx"
DEFAULT_OUTPUT = (
    ROOT / "data" / "samples" / "platform_mappings.immutable.json"
)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile operator-owned platform_mappings.xlsx into one "
            "immutable product identity mapping JSON."
        )
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    _configure_console()
    args = _build_parser().parse_args()
    compiled = compile_product_mapping_workbook(args.source)
    write_immutable_product_mapping(compiled, args.output)
    summary = {
        "source": str(args.source.resolve()),
        "output": str(args.output.resolve()),
        "product_mapping_count": len(compiled.records),
        "source_workbook_sha256": compiled.source_workbook_sha256,
        "mapping_version": compiled.mapping_version,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
