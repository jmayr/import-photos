#!/usr/bin/env python3
"""
Import photos from the Apple Photos library into ~/Pictures organized by date.

Directory structure: ~/Pictures/{year}/{month}/{day}/{filename}

Uses osxphotos to read the Photos library database. Only locally available
photos are imported — iCloud-only files are skipped.

Usage:
    python3 import-photos-library.py              # import from default library
    python3 import-photos-library.py --dry-run    # preview without copying
    python3 import-photos-library.py --library /path/to/Photos Library.photoslibrary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import osxphotos
except ImportError:
    print(
        "Error: osxphotos is required. Install with:\n"
        "  pip3 install --user --break-system-packages osxphotos",
        file=sys.stderr,
    )
    sys.exit(1)

from pipeline import (
    PICTURES_DIR,
    FileContext,
    ImportConfig,
    build_default_pipeline,
)

# ---------------------------------------------------------------------------
# Photos-library-specific extract_date step
# ---------------------------------------------------------------------------


def extract_date(ctx: FileContext, config: ImportConfig) -> None:
    """Read the date from Photos library metadata (pre-populated)."""
    date = ctx.metadata.get("date")
    if date is None:
        ctx.skipped = True
        ctx.skip_reason = "no date available"
        return
    ctx.metadata["date_source"] = "photos_library"


# ---------------------------------------------------------------------------
# Photo collection
# ---------------------------------------------------------------------------


def collect_photos(
    library_path: str | None = None,
) -> list[tuple[osxphotos.PhotoInfo, list[Path]]]:
    """
    Read the Photos library and return a list of (photo, file_paths) tuples.

    Each photo may produce multiple file paths (e.g. JPEG + RAW pair).
    Only locally available photos are included.
    """
    if library_path:
        db = osxphotos.PhotosDB(dbfile=library_path)
    else:
        db = osxphotos.PhotosDB()

    results: list[tuple[osxphotos.PhotoInfo, list[Path]]] = []

    for photo in db.photos():
        if photo.ismissing:
            continue

        paths: list[Path] = []

        # Primary file (JPEG, HEIC, etc.)
        if photo.path:
            paths.append(Path(photo.path))

        # Associated RAW file if this is a RAW+JPEG pair
        if photo.has_raw and photo.path_raw:
            paths.append(Path(photo.path_raw))

        if paths:
            results.append((photo, paths))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import photos from Apple Photos library"
    )
    parser.add_argument(
        "--library",
        type=str,
        default=None,
        help="Path to Photos library (uses default library if omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be imported without copying",
    )
    args = parser.parse_args()

    # Open library
    print("Reading Photos library...")
    try:
        photo_entries = collect_photos(args.library)
    except Exception as e:
        print(f"Error: Could not read Photos library: {e}", file=sys.stderr)
        sys.exit(1)

    if not photo_entries:
        print("No locally available photos found.")
        sys.exit(0)

    total_files = sum(len(paths) for _, paths in photo_entries)
    print(f"Found {len(photo_entries)} photos ({total_files} files) available locally")

    source_label = args.library or "Apple Photos (default library)"
    print(f"Source:      {source_label}")
    print(f"Destination: {PICTURES_DIR}")
    if args.dry_run:
        print("Mode:        DRY RUN (no files will be copied)")
    print()

    # Build pipeline: prepend Photos extract_date before shared steps
    pipeline = build_default_pipeline()
    pipeline.add_step(extract_date, before="resolve_target")

    # source path is not meaningful for the Photos library, use PICTURES_DIR
    config = ImportConfig(source=PICTURES_DIR, dry_run=args.dry_run)

    imported = 0
    skipped = 0
    errors = 0

    for photo, paths in photo_entries:
        for file_path in paths:
            # Use the original camera filename, but keep the actual extension
            # from the library file (Photos may store as .heic, .jpeg, etc.)
            original_name = photo.original_filename
            original_stem = Path(original_name).stem
            actual_ext = file_path.suffix
            dest_filename = original_stem + actual_ext

            ctx = FileContext(src_path=file_path)
            # Override the filename used for the destination
            ctx.metadata["dest_filename"] = dest_filename
            # Pre-populate date from Photos library (timezone-aware datetime)
            ctx.metadata["date"] = photo.date

            try:
                pipeline.run(ctx, config)
            except Exception as e:
                print(f"  ERROR {dest_filename}: {e}", file=sys.stderr)
                errors += 1
                continue

            if ctx.skipped:
                skipped += 1
            else:
                imported += 1
                action = "Would copy" if args.dry_run else "Copied"
                print(
                    f"  {action} {dest_filename} -> "
                    f"{ctx.dest_path.relative_to(PICTURES_DIR)}"
                )

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
