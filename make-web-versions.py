#!/usr/bin/env python3
"""
Generate web-optimized JPEGs for existing photos in ~/Pictures.

Processes a given year, month, or year/month range, creating web versions
for any photo that doesn't already have one in the web/ subfolder.

Usage:
    python3 make-web-versions.py 2025              # all of 2025
    python3 make-web-versions.py 2025/07           # July 2025 only
    python3 make-web-versions.py 2025 --dry-run    # preview without creating files
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import (
    PICTURES_DIR,
    FileContext,
    ImportConfig,
    WEB_SKIP_EXTENSIONS,
    WEB_SUPPORTED_EXTENSIONS,
    make_web_version,
)

# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def collect_photos(base_dir: Path) -> list[Path]:
    """Collect all supported photo files under base_dir (recursively).

    Skips files in web/ subdirectories and dotfiles.
    """
    files: list[Path] = []

    for f in sorted(base_dir.rglob("*")):
        if not f.is_file():
            continue
        # Skip dotfiles (macOS ._ resource forks, .DS_Store, etc.)
        if f.name.startswith("."):
            continue
        # Skip files already inside a web/ directory
        if "web" in f.relative_to(base_dir).parts:
            continue

        suffix = f.suffix.lower()
        if suffix in WEB_SKIP_EXTENSIONS:
            continue
        if suffix not in WEB_SUPPORTED_EXTENSIONS:
            continue

        files.append(f)

    return files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate web versions for existing photos in ~/Pictures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 make-web-versions.py 2025              # all of 2025
  python3 make-web-versions.py 2025/07           # July 2025 only
  python3 make-web-versions.py 2025 --dry-run    # preview without creating files

Path format:
  YYYY     - Process entire year (e.g., 2025)
  YYYY/MM  - Process specific month (e.g., 2025/07)
""",
    )
    parser.add_argument(
        "path",
        type=str,
        metavar="YYYY|YYYY/MM",
        help="Year or year/month to process (e.g. 2025 or 2025/07)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be created without writing files",
    )
    args = parser.parse_args()

    target_dir = PICTURES_DIR / args.path

    if not target_dir.is_dir():
        print(
            f"Error: {target_dir} is not a directory\n"
            f"\n"
            f"  Expected: YYYY or YYYY/MM (e.g. 2025 or 2025/07)\n"
            f"  This resolves to: {PICTURES_DIR}/YYYY/MM",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Source: {target_dir}")
    if args.dry_run:
        print("Mode:   DRY RUN (no files will be created)")
    print()

    # Collect photos
    files = collect_photos(target_dir)
    if not files:
        print("No photos found.")
        sys.exit(0)

    print(f"Found {len(files)} photos")
    print()

    # Run make_web_version on each file
    # We reuse the pipeline step directly — it expects ctx.dest_path to be
    # the photo to process, which for existing files is the file itself.
    config = ImportConfig(source=target_dir, dry_run=args.dry_run)

    created = 0
    skipped = 0
    errors = 0

    for f in files:
        ctx = FileContext(src_path=f)
        ctx.dest_path = f  # the photo is already in place

        try:
            make_web_version(ctx, config)
        except Exception as e:
            print(f"  ERROR {f.name}: {e}", file=sys.stderr)
            errors += 1
            continue

        web_path = ctx.metadata.get("web_path")
        if web_path:
            created += 1
            rel = f.relative_to(PICTURES_DIR)
            action = "Would create" if args.dry_run else "Created"
            print(f"  {action} web/{f.stem}.jpg  ({rel})")
        else:
            skipped += 1

    # Summary
    print()
    print("=" * 50)
    print(f"  Created: {created}")
    print(f"  Skipped: {skipped} (web version exists)")
    if errors:
        print(f"  Errors:  {errors}")
    print("=" * 50)

    if args.dry_run and created > 0:
        print("\nRun without --dry-run to actually create web versions.")


if __name__ == "__main__":
    main()
