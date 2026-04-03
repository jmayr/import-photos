#!/usr/bin/env python3
"""
Repair mismatched .analysis.json sidecar files.

When analyze-day.py was run before the filename fix, the AI invented sequential
filenames (IMG_001.jpg, IMG_002.jpg, ...) instead of using real ones.
This script re-maps orphaned sidecars back to the correct files by position.

Usage:
    python3 repair-sidecars.py 2025/07/15
    python3 repair-sidecars.py 2025/07/15 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pipeline import PICTURES_DIR


def extract_index(stem: str) -> int | None:
    """
    Extract a numeric index from an AI-invented filename stem.

    Handles patterns like:
      IMG_001   → 1
      IMG_1     → 1
      image_003 → 3
      photo3    → 3
      003       → 3
    Returns None if no number found.
    """
    m = re.search(r"(\d+)$", stem)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", stem)
    if m:
        return int(m.group(1))
    return None


def repair_day(day_dir: Path, dry_run: bool) -> None:
    web_dir = day_dir / "web"

    if not web_dir.exists():
        print(f"Error: No web/ folder found at {web_dir}", file=sys.stderr)
        sys.exit(1)

    # Real JPEG files on disk, sorted (same order as load_web_images)
    real_files = sorted(web_dir.glob("*.jpg"))

    if not real_files:
        print(f"Error: No .jpg files found in {web_dir}", file=sys.stderr)
        sys.exit(1)

    # All sidecars
    all_sidecars = list(web_dir.glob("*.jpg.analysis.json"))

    if not all_sidecars:
        print("No sidecar files found — nothing to repair.")
        return

    # Split sidecars into matched (real file exists) and orphaned (no matching file)
    matched: list[Path] = []
    orphaned: list[Path] = []

    for sidecar in all_sidecars:
        # Sidecar "foo.jpg.analysis.json" → image "foo.jpg"
        img_name = sidecar.name[: -len(".analysis.json")]
        if (web_dir / img_name).exists():
            matched.append(sidecar)
        else:
            orphaned.append(sidecar)

    print(f"  Real JPEG files:   {len(real_files)}")
    print(f"  Matched sidecars:  {len(matched)}")
    print(f"  Orphaned sidecars: {len(orphaned)}")

    if not orphaned:
        print("\nAll sidecars already match their image files — nothing to repair.")
        return

    # Determine which real files are NOT yet covered by a matched sidecar
    matched_img_names = {s.name[: -len(".analysis.json")] for s in matched}
    uncovered_real = [f for f in real_files if f.name not in matched_img_names]

    print(f"  Uncovered real files: {len(uncovered_real)}")

    if len(orphaned) != len(uncovered_real):
        print(
            f"\nWarning: {len(orphaned)} orphaned sidecar(s) but {len(uncovered_real)} "
            f"uncovered real file(s) — counts don't match.\n"
            f"Cannot safely repair by position. Please repair manually.",
            file=sys.stderr,
        )
        print("\nOrphaned sidecars:")
        for s in sorted(orphaned):
            print(f"  {s.name}")
        print("\nUncovered real files:")
        for f in uncovered_real:
            print(f"  {f.name}")
        sys.exit(1)

    # Sort orphaned sidecars by the numeric index in their invented name
    def sort_key(sidecar: Path) -> tuple[int, str]:
        stem = Path(sidecar.name[: -len(".analysis.json")]).stem  # e.g. "IMG_003"
        idx = extract_index(stem)
        return (idx if idx is not None else 99999, sidecar.name)

    orphaned_sorted = sorted(orphaned, key=sort_key)

    # uncovered_real is already sorted alphabetically (same order as load_web_images)
    print()
    if dry_run:
        print("Dry run — no files will be changed:\n")
    else:
        print("Repairing:\n")

    for sidecar, real_file in zip(orphaned_sorted, uncovered_real):
        old_sidecar_name = sidecar.name
        new_sidecar_name = f"{real_file.stem}.jpg.analysis.json"
        new_sidecar_path = web_dir / new_sidecar_name

        invented_img = sidecar.name[: -len(".analysis.json")]

        print(f"  {old_sidecar_name}")
        print(f"    → {new_sidecar_name}  (maps to {real_file.name})")

        if not dry_run:
            if new_sidecar_path.exists():
                print(
                    f"    Warning: {new_sidecar_name} already exists, skipping.",
                    file=sys.stderr,
                )
                continue

            # Update the description/filename fields inside the sidecar JSON
            try:
                with open(sidecar) as f:
                    data = json.load(f)
            except Exception as e:
                print(f"    Warning: Could not read sidecar: {e}", file=sys.stderr)
                continue

            # The sidecar doesn't store a self-referential filename field,
            # but best_picks / groups references inside might use the invented name.
            # Those live in the markdown, not the sidecar — so just rename the file.

            sidecar.rename(new_sidecar_path)

    if not dry_run:
        print("\nDone.")
    else:
        print("\nRun without --dry-run to apply changes.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair mismatched .analysis.json sidecar files"
    )
    parser.add_argument(
        "path",
        type=str,
        help="Year/month/day to repair (e.g. 2025/07/15)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be renamed without making changes",
    )

    args = parser.parse_args()

    target_dir = PICTURES_DIR / args.path

    if not target_dir.is_dir():
        print(
            f"Error: {target_dir} is not a directory\n"
            f"Expected: YYYY/MM/DD (e.g. 2025/07/15)",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Repairing sidecars in: {target_dir}")
    print()
    repair_day(target_dir, args.dry_run)


if __name__ == "__main__":
    main()
