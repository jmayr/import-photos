"""
Shared pipeline for photo import scripts.

Provides the core building blocks used by both the SD card importer
and the Apple Photos library importer. Any steps added to
build_default_pipeline() are automatically used by both.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

# Register HEIC support (pillow-heif)
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass  # HEIC support unavailable — only needed for Photos library imports

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PICTURES_DIR = Path.home() / "Pictures"

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
        raise ValueError(f"Pipeline step '{name}' not found. Available: {self.step_names}")


# ---------------------------------------------------------------------------
# Import configuration
# ---------------------------------------------------------------------------


@dataclass
class ImportConfig:
    source: Path
    dest_root: Path = PICTURES_DIR
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Shared pipeline steps
# ---------------------------------------------------------------------------


def resolve_target(ctx: FileContext, config: ImportConfig) -> None:
    """Compute the destination path: ~/Pictures/YYYY/MM/DD/filename."""
    date: datetime = ctx.metadata["date"]
    year = f"{date.year}"
    month = f"{date.month:02d}"
    day = f"{date.day:02d}"
    filename = ctx.metadata.get("dest_filename", ctx.src_path.name)
    ctx.dest_path = config.dest_root / year / month / day / filename


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
# Web version settings
# ---------------------------------------------------------------------------

WEB_MAX_SIZE = 2048  # longest edge in pixels
WEB_QUALITY = 80  # JPEG quality (1-100)
WEB_SUBDIR = "web"
WEB_SKIP_EXTENSIONS = {".raf"}  # RAW files — skip, not useful without editing
WEB_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".png", ".tiff", ".tif"}


def make_web_version(ctx: FileContext, config: ImportConfig) -> None:
    """Create a web-optimized JPEG in a web/ subfolder next to the original.

    - Resizes to fit within 2048x2048 (preserves aspect ratio, never upscales)
    - Auto-rotates based on EXIF orientation
    - Converts to RGB JPEG at 80% quality
    - Skips RAW files (.raf)
    - Skips if the web version already exists
    """
    suffix = ctx.dest_path.suffix.lower()
    if suffix in WEB_SKIP_EXTENSIONS:
        return
    if suffix not in WEB_SUPPORTED_EXTENSIONS:
        return

    web_dir = ctx.dest_path.parent / WEB_SUBDIR
    web_path = web_dir / (ctx.dest_path.stem + ".jpg")

    if web_path.exists():
        return

    ctx.metadata["web_path"] = web_path

    if config.dry_run:
        return

    web_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(ctx.dest_path)
    img = ImageOps.exif_transpose(img)  # auto-rotate
    img.thumbnail((WEB_MAX_SIZE, WEB_MAX_SIZE), Image.LANCZOS)

    # Ensure RGB (HEIC can be RGBA, CMYK, etc.)
    if img.mode not in ("RGB",):
        img = img.convert("RGB")

    img.save(web_path, "JPEG", quality=WEB_QUALITY)


# ---------------------------------------------------------------------------
# Default pipeline
# ---------------------------------------------------------------------------


def build_default_pipeline() -> Pipeline:
    """
    Construct the default import pipeline.

    Both importers prepend their own source-specific extract_date step
    before resolve_target. Any steps added here are shared by all importers.
    """
    pipeline = Pipeline()
    pipeline.add_step(resolve_target)
    pipeline.add_step(check_duplicate)
    pipeline.add_step(copy_file)
    pipeline.add_step(make_web_version)
    return pipeline
