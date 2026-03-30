#!/usr/bin/env python3
"""
Import photos from an SD card into ~/Pictures organized by date.

Directory structure: ~/Pictures/{year}/{month}/{day}/{filename}

Usage:
    python3 import-photos.py              # auto-detect SD card, import
    python3 import-photos.py --dry-run    # preview without copying
    python3 import-photos.py --source /path/to/card
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PICTURES_DIR = Path.home() / "Pictures"
VOLUMES_DIR = Path("/Volumes")
FILE_EXTENSIONS = {".jpg", ".jpeg", ".raf"}
DCIM_FUJI_GLOB = "DCIM/*_FUJI"
EXIFTOOL_BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# FileContext — carries state for one file through the pipeline
# ---------------------------------------------------------------------------


@dataclass
class FileContext:
    """All state associated with a single file being processed."""

    src_path: Path
    dest_path: Path | None = None
    metadata: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

StepFn = Callable[[FileContext, "ImportConfig"], None]


class Pipeline:
    """Ordered list of processing steps applied to each file."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, StepFn]] = []

    def add_step(
        self,
        fn: StepFn,
        name: str | None = None,
        *,
        after: str | None = None,
        before: str | None = None,
    ) -> None:
        """Register a step. Optionally place it after/before a named step."""
        step_name = name or fn.__name__
        entry = (step_name, fn)

        if after:
            idx = self._index_of(after)
            self._steps.insert(idx + 1, entry)
        elif before:
            idx = self._index_of(before)
            self._steps.insert(idx, entry)
        else:
            self._steps.append(entry)

    def run(self, ctx: FileContext, config: "ImportConfig") -> None:
        """Run all steps on a single FileContext. Stops early if skipped."""
        for step_name, fn in self._steps:
            if ctx.skipped:
                return
            fn(ctx, config)

    @property
    def step_names(self) -> list[str]:
        return [name for name, _ in self._steps]

    def _index_of(self, name: str) -> int:
        for i, (n, _) in enumerate(self._steps):
            if n == name:
                return i
        raise ValueError(
            f"Pipeline step '{name}' not found. Available: {self.step_names}"
        )


# ---------------------------------------------------------------------------
# Import configuration
# ---------------------------------------------------------------------------


@dataclass
class ImportConfig:
    source: Path
    dest_root: Path = PICTURES_DIR
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def extract_date(ctx: FileContext, config: ImportConfig) -> None:
    """Read the date from pre-loaded metadata (batch-extracted earlier)."""
    raw = ctx.metadata.get("DateTimeOriginal")
    if not raw:
        # Fallback: use file modification time
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


def resolve_target(ctx: FileContext, config: ImportConfig) -> None:
    """Compute the destination path: ~/Pictures/YYYY/MM/DD/filename."""
    date: datetime = ctx.metadata["date"]
    year = f"{date.year}"
    month = f"{date.month:02d}"
    day = f"{date.day:02d}"
    ctx.dest_path = config.dest_root / year / month / day / ctx.src_path.name


def check_duplicate(ctx: FileContext, config: ImportConfig) -> None:
    """Skip if the file already exists at the target location."""
    if ctx.dest_path and ctx.dest_path.exists():
        ctx.skipped = True
        ctx.skip_reason = "already exists"


def copy_file(ctx: FileContext, config: ImportConfig) -> None:
    """Copy the file to the destination, preserving metadata."""
    if config.dry_run:
        return

    ctx.dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ctx.src_path, ctx.dest_path)


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
            # Files in this batch will fall back to mtime

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


def build_default_pipeline() -> Pipeline:
    """Construct the default import pipeline."""
    pipeline = Pipeline()
    pipeline.add_step(extract_date)
    pipeline.add_step(resolve_target)
    pipeline.add_step(check_duplicate)
    pipeline.add_step(copy_file)
    return pipeline


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

    # Build pipeline and process
    pipeline = build_default_pipeline()
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
