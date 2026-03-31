# import-photos

Import photos into `~/Pictures` organized by date, from two sources:

- **SD card** (`import-photos.py`) -- Fuji camera SD cards
- **Apple Photos** (`import-photos-library.py`) -- the macOS Photos library

Both scripts share a common pipeline (`pipeline.py`), so any processing steps you add are used by both importers.

```
~/Pictures/
  2026/
    01/
      27/
        DSCF6979.JPG
        DSCF6979.RAF
        IMG_6868.heic
        ...
    03/
      20/
        DSCF7119.JPG
        IMG_7496.heic
        ...
```

## Requirements

- Python 3.10+
- [exiftool](https://exiftool.org/) (for SD card import)
- [osxphotos](https://github.com/RhetTbull/osxphotos) (for Photos library import)

```bash
brew install exiftool
pip3 install --user --break-system-packages osxphotos
```

## Usage

### Import from SD card

```bash
# Auto-detect SD card and import
python3 import-photos.py

# Preview what would be imported (no files copied)
python3 import-photos.py --dry-run

# Specify the SD card path manually
python3 import-photos.py --source /Volumes/Untitled
```

### Import from Apple Photos

```bash
# Import from default Photos library
python3 import-photos-library.py

# Preview what would be imported
python3 import-photos-library.py --dry-run

# Specify a Photos library path
python3 import-photos-library.py --library "/path/to/Photos Library.photoslibrary"
```

## How it works

### SD card importer

1. Detects a mounted Fuji SD card by scanning `/Volumes/*/DCIM/*_FUJI/`
2. Collects all `.JPG` and `.RAF` files from the card's `DCIM/` directory
3. Batch-reads `DateTimeOriginal` from EXIF using exiftool
4. Copies each file to `~/Pictures/{year}/{month}/{day}/{filename}`
5. Skips files that already exist at the target path

### Photos library importer

1. Reads the Apple Photos database using osxphotos
2. Skips iCloud-only photos that aren't downloaded locally
3. Uses the photo's creation date from the Photos database
4. Preserves the original camera filename (e.g. `IMG_1234.heic`)
5. Handles RAW+JPEG pairs (both files are imported)
6. Copies each file to `~/Pictures/{year}/{month}/{day}/{filename}`
7. Skips files that already exist at the target path

### What the scripts do NOT do

- Delete or modify source files
- Rename files
- Overwrite existing files in `~/Pictures`

## Duplicate detection

A file is skipped if a file with the same name already exists in the target directory (`~/Pictures/YYYY/MM/DD/filename`). Re-running either script is safe and will only copy new files.

## Project structure

```
import-photos/
  pipeline.py              # shared pipeline, steps, and data types
  import-photos.py         # SD card importer
  import-photos-library.py # Apple Photos importer
```

## Pipeline architecture

Each file passes through an ordered list of processing steps. The shared default pipeline is defined in `pipeline.py`:

| Step             | Description                                    |
| ---------------- | ---------------------------------------------- |
| `resolve_target` | Compute `~/Pictures/YYYY/MM/DD/filename`       |
| `check_duplicate`| Skip if file already exists at target          |
| `copy_file`      | Copy with `shutil.copy2` (preserves metadata)  |

Each importer prepends its own `extract_date` step that sets `ctx.metadata["date"]`:

- **SD card**: reads EXIF `DateTimeOriginal` via exiftool (falls back to file mtime)
- **Photos library**: reads the creation date from the Photos database

### Adding custom steps

To add processing that applies to both importers (e.g. generate thumbnails, convert RAW), add the step to `build_default_pipeline()` in `pipeline.py`:

```python
# pipeline.py

def my_custom_step(ctx: FileContext, config: ImportConfig) -> None:
    """Example: log every imported file."""
    if not config.dry_run:
        print(f"  Processed: {ctx.dest_path.name}")

def build_default_pipeline() -> Pipeline:
    pipeline = Pipeline()
    pipeline.add_step(resolve_target)
    pipeline.add_step(check_duplicate)
    pipeline.add_step(copy_file)
    pipeline.add_step(my_custom_step)  # runs in both importers
    return pipeline
```

To add a step to only one importer, add it in that script's `main()` instead:

```python
pipeline = build_default_pipeline()
pipeline.add_step(extract_date, before="resolve_target")
pipeline.add_step(my_sd_card_only_step, after="copy_file")  # only this importer
```

### Step function signature

A step function receives two arguments:

- **`ctx`** -- `FileContext` with:
  - `src_path` -- original file path
  - `dest_path` -- resolved destination in `~/Pictures` (set by `resolve_target`)
  - `metadata` -- dict with `date`, `date_source`, and source-specific data
  - `skipped` -- set to `True` to stop processing this file
  - `skip_reason` -- optional reason string
- **`config`** -- `ImportConfig` with `source`, `dest_root`, and `dry_run`

### Placement options

```python
pipeline.add_step(my_step)                            # append to end
pipeline.add_step(my_step, after="copy_file")         # insert after a named step
pipeline.add_step(my_step, before="check_duplicate")  # insert before a named step
```

Inspect the current step order:

```python
print(pipeline.step_names)
# ['extract_date', 'resolve_target', 'check_duplicate', 'copy_file']
```
