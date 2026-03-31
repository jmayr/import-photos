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
    return pipeline
