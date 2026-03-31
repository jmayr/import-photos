#!/usr/bin/env python3
"""
Import photos from a Fuji SD card into ~/Pictures organized by date.

Directory structure: ~/Pictures/{year}/{month}/{day}/{filename}

Usage:
    python3 import-photos.py              # auto-detect SD card, import
    python3 import-photos.py --dry-run    # preview without copying
    python3 import-photos.py --source /path/to/card
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from pipeline import (
    PICTURES_DIR,
    FileContext,
    ImportConfig,
    build_default_pipeline,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VOLUMES_DIR = Path("/Volumes")
FILE_EXTENSIONS = {".jpg", ".jpeg", ".raf"}
DCIM_FUJI_GLOB = "DCIM/*_FUJI"
EXIFTOOL_BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# SD-card-specific extract_date step
# ---------------------------------------------------------------------------


def extract_date(ctx: FileContext, config: ImportConfig) -> None:
    """Read the date from pre-loaded EXIF metadata (batch-extracted earlier)."""
    raw = ctx.metadata.get("DateTimeOriginal")
    if not raw:
        mtime = ctx.src_path.stat().st_mtime
        ctx.metadata["date"] = datetime.fromtimestamp(mtime)
        ctx.metadata["date_source"] = "mtime"
        return

    try:
        ctx.metadata["date"] = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
        ctx.metadata["date_source"] = "exif"
    except ValueError:
        mtime = ctx.src_path.stat().st_mtime
        ctx.metadata["date"] = datetime.fromtimestamp(mtime)
        ctx.metadata["date_source"] = "mtime"


# ---------------------------------------------------------------------------
# Batch EXIF extraction
# ---------------------------------------------------------------------------


def batch_extract_exif(files: list[Path]) -> dict[str, dict]:
    """
    Call exiftool once per batch to extract DateTimeOriginal for all files.
    Returns a dict mapping source file path (str) -> metadata dict.
    """
    result: dict[str, dict] = {}

    for i in range(0, len(files), EXIFTOOL_BATCH_SIZE):
        batch = files[i : i + EXIFTOOL_BATCH_SIZE]
        cmd = ["exiftool", "-DateTimeOriginal", "-json"] + [str(f) for f in batch]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            entries = json.loads(proc.stdout)
            for entry in entries:
                src = entry.get("SourceFile", "")
                result[src] = entry
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"  Warning: exiftool batch failed: {e}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# SD card detection
# ---------------------------------------------------------------------------


def find_sd_card() -> Path | None:
    """Auto-detect a mounted SD card with a DCIM/*_FUJI directory."""
    if not VOLUMES_DIR.exists():
        return None

    candidates: list[Path] = []
    for volume in VOLUMES_DIR.iterdir():
        if volume.name == "Macintosh HD":
            continue
        fuji_dirs = list(volume.glob(DCIM_FUJI_GLOB))
        if fuji_dirs:
            candidates.append(volume)

    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        print("Multiple SD cards found:")
        for i, c in enumerate(candidates):
            print(f"  [{i + 1}] {c}")
        choice = input("Select card number: ").strip()
        try:
            return candidates[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid selection.", file=sys.stderr)
            return None
    return None


def collect_files(source: Path) -> list[Path]:
    """Find all importable image files on the SD card."""
    files: list[Path] = []
    dcim = source / "DCIM"
    if not dcim.exists():
        print(f"Error: No DCIM directory found at {source}", file=sys.stderr)
        return files

    for f in sorted(dcim.rglob("*")):
        if (
            f.is_file()
            and f.suffix.lower() in FILE_EXTENSIONS
            and not f.name.startswith(".")
        ):
            files.append(f)

    return files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Import photos from SD card")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to SD card root (auto-detected if omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be imported without copying",
    )
    args = parser.parse_args()

    # Resolve source
    source = args.source
    if source is None:
        source = find_sd_card()
        if source is None:
            print(
                "Error: No SD card found. Use --source to specify manually.",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"Source:      {source}")
    print(f"Destination: {PICTURES_DIR}")
    if args.dry_run:
        print("Mode:        DRY RUN (no files will be copied)")
    print()

    # Collect files
    files = collect_files(source)
    if not files:
        print("No importable files found.")
        sys.exit(0)

    print(f"Found {len(files)} files on card")

    # Batch extract EXIF data
    print("Reading EXIF data...")
    exif_data = batch_extract_exif(files)
    print()

    # Build pipeline: prepend SD-card extract_date before shared steps
    pipeline = build_default_pipeline()
    pipeline.add_step(extract_date, before="resolve_target")
    config = ImportConfig(source=source, dry_run=args.dry_run)

    imported = 0
    skipped = 0
    errors = 0

    for f in files:
        ctx = FileContext(src_path=f)

        # Pre-populate metadata from batch EXIF extraction
        exif_entry = exif_data.get(str(f), {})
        ctx.metadata.update(exif_entry)

        try:
            pipeline.run(ctx, config)
        except Exception as e:
            print(f"  ERROR {f.name}: {e}", file=sys.stderr)
            errors += 1
            continue

        if ctx.skipped:
            skipped += 1
        else:
            imported += 1
            action = "Would copy" if args.dry_run else "Copied"
            print(f"  {action} {f.name} -> {ctx.dest_path.relative_to(PICTURES_DIR)}")

    # Summary
    print()
    print("=" * 50)
    print(f"  Imported: {imported}")
    print(f"  Skipped:  {skipped} (already exist)")
    if errors:
        print(f"  Errors:   {errors}")
    print("=" * 50)

    if args.dry_run and imported > 0:
        print("\nRun without --dry-run to actually import.")


if __name__ == "__main__":
    main()
