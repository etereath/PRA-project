from __future__ import annotations

import argparse
import hashlib
import shutil
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "shadowbot" / "test2"
DEFAULT_APP_DIR = Path(
    r"C:\Users\etere\AppData\Local\ShadowBot\users\940455499808497666\apps"
    r"\fb717589-c95c-4228-935d-c61d54df494c\xbot_robot"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize canonical test2 Python code into the ShadowBot app")
    parser.add_argument("--app-dir", type=Path, default=DEFAULT_APP_DIR)
    parser.add_argument("--check", action="store_true", help="Only compare hashes; do not write files")
    return parser


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync(app_dir: Path, *, check_only: bool) -> list[dict[str, str]]:
    if not app_dir.exists():
        raise FileNotFoundError(app_dir)
    records: list[dict[str, str]] = []
    mappings = (
        (SOURCE_DIR / "module1.py", app_dir / "module1.py"),
        (SOURCE_DIR / "vertical_slice_read_price.py", app_dir / "vertical_slice_read_price.py"),
        (SOURCE_DIR / "shadowbot_queue_worker.py", app_dir / "shadowbot_queue_worker.py"),
    )
    for source, destination in mappings:
        source_hash = sha256(source)
        destination_hash = sha256(destination) if destination.exists() else ""
        status = "CURRENT" if source_hash == destination_hash else "DIFFERENT"
        if not check_only and status == "DIFFERENT":
            if destination.exists():
                stamp = datetime.now().strftime("%Y%m%d%H%M%S")
                shutil.copy2(destination, destination.with_name(destination.name + f".bak_queue_{stamp}"))
            shutil.copy2(source, destination)
            destination_hash = sha256(destination)
            status = "SYNCED"
        records.append(
            {
                "source": str(source),
                "destination": str(destination),
                "source_sha256": source_hash,
                "destination_sha256": destination_hash,
                "status": status,
            }
        )
    config_source = SOURCE_DIR / "shadowbot_worker_config.example.json"
    config_destination = app_dir / "shadowbot_worker_config.json"
    if not check_only and not config_destination.exists():
        shutil.copy2(config_source, config_destination)
        records.append({"source": str(config_source), "destination": str(config_destination), "status": "CREATED"})
    elif config_destination.exists():
        records.append({"source": str(config_source), "destination": str(config_destination), "status": "EXISTS"})
    return records


def main() -> int:
    args = build_parser().parse_args()
    for record in sync(args.app_dir, check_only=args.check):
        print(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
