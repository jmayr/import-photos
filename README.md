# camera-input

Import photos from a Fuji SD card into `~/Pictures`, organized by date.

```
~/Pictures/
  2026/
    01/
      27/
        DSCF6979.JPG
        DSCF6979.RAF
        DSCF6980.JPG
        ...
    03/
      20/
        DSCF7119.JPG
        ...
```

## Requirements

- Python 3.10+
- [exiftool](https://exiftool.org/) (used to read EXIF dates from both JPG and RAF files)

```bash
brew install exiftool
```

## Usage

```bash
# Auto-detect SD card and import
python3 import-photos.py

# Preview what would be imported (no files copied)
python3 import-photos.py --dry-run

# Specify the SD card path manually
python3 import-photos.py --source /Volumes/Untitled
```

### What it does

1. Detects a mounted Fuji SD card by scanning `/Volumes/*/DCIM/*_FUJI/`
2. Collects all `.JPG` and `.RAF` files from the card's `DCIM/` directory
3. Batch-reads `DateTimeOriginal` from EXIF using exiftool
4. Copies each file to `~/Pictures/{year}/{month}/{day}/{filename}`
5. Skips files that already exist at the target path
6. Prints a summary of imported/skipped/errored files

### What it does NOT do

- Delete or modify files on the SD card
- Rename files
- Overwrite existing files in `~/Pictures`

## Duplicate detection

A file is skipped if a file with the same name already exists in the target directory (`~/Pictures/YYYY/MM/DD/filename`). This means re-running the script after an import is safe and will only copy new files.

## Pipeline architecture

Each file passes through an ordered list of processing steps. The default pipeline:

| Step             | Description                                     |
| ---------------- | ----------------------------------------------- |
| `extract_date`   | Read EXIF date (falls back to file mtime)       |
| `resolve_target` | Compute `~/Pictures/YYYY/MM/DD/filename`        |
| `check_duplicate`| Skip if file already exists at target           |
| `copy_file`      | Copy with `shutil.copy2` (preserves metadata)   |

### Adding custom steps

To add processing after import (e.g. generate thumbnails, convert RAW, add to a catalog), define a step function and insert it into the pipeline:

```python
from import_photos import FileContext, ImportConfig, build_default_pipeline

def generate_thumbnail(ctx: FileContext, config: ImportConfig) -> None:
    """Create a thumbnail for imported JPGs."""
    if ctx.dest_path.suffix.lower() != ".jpg":
        return
    # your thumbnail logic here
    print(f"  Thumbnail: {ctx.dest_path.name}")

pipeline = build_default_pipeline()
pipeline.add_step(generate_thumbnail, after="copy_file")
```

A step function receives:

- **`ctx.src_path`** -- original file path on the SD card
- **`ctx.dest_path`** -- resolved destination path in `~/Pictures`
- **`ctx.metadata`** -- dict with EXIF data (`DateTimeOriginal`, `date`, `date_source`, etc.)
- **`ctx.skipped`** -- set to `True` to stop processing this file
- **`ctx.skip_reason`** -- optional reason string
- **`config`** -- `ImportConfig` with `source`, `dest_root`, and `dry_run`

Placement options:

```python
pipeline.add_step(my_step)                        # append to end
pipeline.add_step(my_step, after="copy_file")     # insert after a named step
pipeline.add_step(my_step, before="check_duplicate")  # insert before a named step
```

You can inspect the current step order with:

```python
print(pipeline.step_names)
# ['extract_date', 'resolve_target', 'check_duplicate', 'copy_file']
```
