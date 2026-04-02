#!/usr/bin/env python3
"""
Analyze photos in a day folder using AI vision models.

Analyzes images, rates them (1-10), groups by visual similarity/location/time,
and generates a markdown summary with best picks.

Usage:
    python3 analyze-day.py 2025/07/15
    python3 analyze-day.py 2025/07/15 --provider claude --api-key sk-ant-...
    python3 analyze-day.py 2025/07/15 --provider ollama --model llama3.2-vision
    python3 analyze-day.py 2025/07/15 --limit 20
    python3 analyze-day.py 2025/07/15 --reanalyze
    python3 analyze-day.py 2025/07/15 --rename-only
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from pipeline import PICTURES_DIR

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_MODEL = "llama3.2-vision"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_OLLAMA_BASE = "http://localhost:11434"

# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------


def load_web_images(
    day_dir: Path, limit: int | None = None
) -> list[tuple[Path, bytes]]:
    """
    Load web preview images from the web/ subfolder.

    Returns list of (filename, jpeg_bytes) tuples.
    """
    web_dir = day_dir / "web"

    if not web_dir.exists():
        print(
            f"Error: No web/ folder found at {web_dir}\n"
            f"Run the import script first to generate web previews.",
            file=sys.stderr,
        )
        sys.exit(1)

    images: list[tuple[Path, bytes]] = []

    for img_path in sorted(web_dir.glob("*.jpg")):
        if limit and len(images) >= limit:
            break

        try:
            with open(img_path, "rb") as f:
                images.append((img_path.name, f.read()))
        except Exception as e:
            print(f"  Warning: Could not load {img_path.name}: {e}", file=sys.stderr)

    if not images:
        print(f"Error: No web images found in {web_dir}", file=sys.stderr)
        sys.exit(1)

    return images


# ---------------------------------------------------------------------------
# AI Analysis - Claude
# ---------------------------------------------------------------------------


def analyze_with_claude(
    images: list[tuple[Path, bytes]],
    date_str: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    """
    Analyze images using Anthropic's Claude Vision API.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        print(
            "Error: anthropic package is required for Claude.\n"
            "Install with: pip3 install --user --break-system-packages anthropic",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Preparing {len(images)} images for Claude API...")

    client = Anthropic(api_key=api_key)

    prompt = f"""Analyze these {len(images)} photos from {date_str}. Return JSON with this exact structure:

{{
  "images": [
    {{"filename": "IMG_001.jpg", "rating": 8.5, "description": "One sentence description"}}
  ],
  "groups": [
    {{
      "name": "Morning Beach Session",
      "description": "Golden hour lighting, beach portraits",
      "time_range": "08:00-10:30",
      "image_filenames": ["IMG_001.jpg", "IMG_003.jpg"],
      "best_picks": ["IMG_003.jpg", "IMG_007.jpg"]
    }}
  ],
  "best_overall": {{"filename": "IMG_023.jpg", "rating": 9.2, "reason": "Perfect moment capture with ideal lighting"}}
}}

Requirements:
- Group by: location changes, visual similarity, time gaps, activity changes
- Best picks per group: 1-3 images with maximum variety/coverage
- Ratings: 1.0-10.0 scale with decimals allowed
- All filenames must match exactly what was provided
- Include all images in groups
- Order images within groups by rating (highest first)

Return ONLY valid JSON, no additional text."""

    # Build message content with images
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

    print(f"  Encoding images (this may take a moment)...")
    for i, (filename, img_bytes) in enumerate(images, 1):
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(img_bytes).decode("utf-8"),
                },
            }
        )
        if i % 10 == 0 or i == len(images):
            print(f"    Prepared {i}/{len(images)} images")

    print(f"  Sending to Claude API...")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": content}],
        )
        print(f"  Received response from Claude")

        # Extract JSON from response
        response_text = response.content[0].text  # type: ignore

        # Try to extract JSON block if wrapped in markdown
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = response_text

        result = json.loads(json_str)

        # Validate structure
        if (
            not isinstance(result, dict)
            or "images" not in result
            or "groups" not in result
        ):
            raise ValueError(
                "Invalid response structure - missing 'images' or 'groups'"
            )

        return result

    except Exception as e:
        print(
            f"Error: Claude API request failed: {e}\n"
            f"Check your API key and internet connection.",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# AI Analysis - Ollama
# ---------------------------------------------------------------------------


def analyze_with_ollama(
    images: list[tuple[Path, bytes]],
    date_str: str,
    model: str,
    base_url: str,
    batch_size: int = 50,
) -> dict[str, Any]:
    """
    Analyze images using Ollama vision model with batching.

    Splits images into batches, analyzes each batch, then merges results.
    """
    import requests

    # If we have more images than batch_size, split into batches
    if len(images) > batch_size:
        print(f"  Splitting {len(images)} images into batches of {batch_size}...")
        batches: list[list[tuple[Path, bytes]]] = []
        for i in range(0, len(images), batch_size):
            batches.append(images[i : i + batch_size])

        all_results: list[dict[str, Any]] = []
        for i, batch in enumerate(batches, 1):
            print(f"\n  Batch {i}/{len(batches)} ({len(batch)} images)")
            try:
                batch_result = _analyze_ollama_batch(
                    batch,
                    date_str,
                    model,
                    base_url,
                    batch_num=i,
                    total_batches=len(batches),
                )
                all_results.append(batch_result)
            except Exception as e:
                print(
                    f"  Warning: Batch {i}/{len(batches)} failed, skipping: {e}",
                    file=sys.stderr,
                )

        if not all_results:
            print(
                "Error: All batches failed. Check Ollama server and model.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Merge results
        return _merge_batch_results(all_results)
    else:
        try:
            return _analyze_ollama_batch(images, date_str, model, base_url)
        except Exception as e:
            print(
                f"Error: Ollama request failed: {e}\n"
                f"Check that Ollama server is running at {base_url}",
                file=sys.stderr,
            )
            sys.exit(1)


def _analyze_ollama_batch(
    images: list[tuple[Path, bytes]],
    date_str: str,
    model: str,
    base_url: str,
    batch_num: int = 1,
    total_batches: int = 1,
) -> dict[str, Any]:
    """
    Analyze a single batch of images with Ollama.
    """
    import requests

    batch_info = f" (batch {batch_num}/{total_batches})" if total_batches > 1 else ""
    prompt = f"""Analyze these {len(images)} photos from {date_str}{batch_info}. Return JSON with this exact structure:

{{
  "images": [
    {{"filename": "IMG_001.jpg", "rating": 8.5, "description": "One sentence description"}}
  ],
  "groups": [
    {{
      "name": "Morning Beach Session",
      "description": "Golden hour lighting, beach portraits",
      "time_range": "08:00-10:30",
      "image_filenames": ["IMG_001.jpg", "IMG_003.jpg"],
      "best_picks": ["IMG_003.jpg", "IMG_007.jpg"]
    }}
  ],
  "best_overall": {{"filename": "IMG_023.jpg", "rating": 9.2, "reason": "Perfect moment capture with ideal lighting"}}
}}

Requirements:
- Group by: location changes, visual similarity, time gaps, activity changes
- Best picks per group: 1-3 images with maximum variety/coverage
- Ratings: 1.0-10.0 scale with decimals allowed
- All filenames must match exactly what was provided
- Include all images in groups
- Order images within groups by rating (highest first)
{f"- This is batch {batch_num} of {total_batches} - focus on grouping within this batch only" if total_batches > 1 else ""}

Return ONLY valid JSON, no additional text."""

    # Resize images for Ollama (512px longest edge to fit 256k context window)
    resized_images: list[bytes] = []
    for i, (filename, img_bytes) in enumerate(images, 1):
        try:
            img = Image.open(BytesIO(img_bytes))
            img.thumbnail((512, 512), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, "JPEG", quality=85)
            resized_images.append(buf.getvalue())
        except Exception as e:
            print(f"  Warning: Could not resize {filename}: {e}", file=sys.stderr)
            resized_images.append(img_bytes)

        if i % 10 == 0 or i == len(images):
            print(f"    Resized {i}/{len(images)} images")

    # Encode as base64
    print(f"  Encoding images to base64...")
    base64_images = [base64.b64encode(img).decode("utf-8") for img in resized_images]
    print(f"  Sending to Ollama at {base_url}...")

    try:
        response = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "images": base64_images,
                "stream": False,
            },
            timeout=300,
        )

        print(f"  Received response from Ollama")

        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")

        response_data = response.json()
        response_text = response_data.get("response", "")

        # Try to extract JSON block if wrapped in markdown
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = response_text

        result = json.loads(json_str)

        # Validate structure
        if (
            not isinstance(result, dict)
            or "images" not in result
            or "groups" not in result
        ):
            raise ValueError(
                "Invalid response structure - missing 'images' or 'groups'"
            )

        return result

    except requests.exceptions.ConnectionError as e:
        print(
            f"Error: Could not connect to Ollama server at {base_url}\n"
            f"Check that Ollama is running: ollama serve\n"
            f"Original error: {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        # Re-raise so multi-batch callers can decide whether to skip this batch
        raise


def _merge_batch_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Merge analysis results from multiple batches.

    Combines images, re-groups across batches, and selects new best picks.
    """
    print(f"\n  Merging {len(results)} batch results...")

    # Combine all images
    all_images: list[dict[str, Any]] = []
    for result in results:
        all_images.extend(result.get("images", []))

    # Sort all images by rating
    all_images.sort(key=lambda x: -x.get("rating", 0))

    # Simple re-grouping: combine groups with similar names or overlapping images
    # For now, just list all groups sequentially and mark the overall best
    all_groups: list[dict[str, Any]] = []
    for i, result in enumerate(results, 1):
        for group in result.get("groups", []):
            new_group = group.copy()
            new_group["name"] = (
                f"{group.get('name', f'Group {len(all_groups) + 1}')} (Batch {i})"
            )
            all_groups.append(new_group)

    # Find overall best image across all batches
    best_overall = (
        max(all_images, key=lambda x: x.get("rating", 0)) if all_images else None
    )

    print(f"    Total images: {len(all_images)}")
    print(f"    Total groups: {len(all_groups)}")

    return {
        "images": all_images,
        "groups": all_groups,
        "best_overall": best_overall,
    }


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def load_cached_analysis(web_dir: Path) -> dict[str, dict[str, Any]]:
    """
    Read all existing *.jpg.analysis.json sidecar files in web_dir.

    Returns a dict mapping filename (e.g. "IMG_001.jpg") -> sidecar data dict.
    """
    cached: dict[str, dict[str, Any]] = {}
    for sidecar_path in web_dir.glob("*.jpg.analysis.json"):
        # Reconstruct the original image filename from the sidecar name.
        # Sidecar pattern: <stem>.jpg.analysis.json  → image: <stem>.jpg
        img_filename = sidecar_path.name[: -len(".analysis.json")]
        try:
            with open(sidecar_path) as f:
                cached[img_filename] = json.load(f)
        except Exception as e:
            print(
                f"  Warning: Could not read sidecar {sidecar_path.name}: {e}",
                file=sys.stderr,
            )
    return cached


def merge_with_cached(
    new_analysis: dict[str, Any] | None,
    cached: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Merge AI analysis results for new images with cached sidecar data.

    - new_analysis: result from the AI provider (may be None if all images cached).
    - cached: {filename: sidecar_dict} for already-analyzed images.

    Cached images that don't appear in any new group are placed in a
    'Previously analyzed' group.
    """
    # Collect new images list
    new_images: list[dict[str, Any]] = []
    new_groups: list[dict[str, Any]] = []
    new_best_overall: dict[str, Any] = {}

    if new_analysis:
        new_images = new_analysis.get("images", [])
        new_groups = new_analysis.get("groups", [])
        new_best_overall = new_analysis.get("best_overall", {}) or {}

    # Build set of filenames already assigned to a new group
    in_new_group: set[str] = set()
    for group in new_groups:
        for fn in group.get("image_filenames", []):
            in_new_group.add(fn)

    # Build "Previously analyzed" group for cached images not in any new group
    prev_filenames: list[str] = [
        fn for fn in sorted(cached.keys()) if fn not in in_new_group
    ]

    prev_group: dict[str, Any] | None = None
    if prev_filenames:
        # Pick best picks from cached: top-3 by rating
        prev_sorted = sorted(
            prev_filenames,
            key=lambda fn: cached[fn].get("rating", 0),
            reverse=True,
        )
        prev_group = {
            "name": "Previously analyzed",
            "description": "Images that were analyzed in a previous run",
            "time_range": "",
            "image_filenames": prev_filenames,
            "best_picks": prev_sorted[:3],
        }

    # Combine image lists
    cached_images: list[dict[str, Any]] = [
        {
            "filename": fn,
            "rating": data.get("rating", 0),
            "description": data.get("description", ""),
        }
        for fn, data in cached.items()
    ]
    all_images = new_images + cached_images

    # Combine groups
    all_groups = new_groups + ([prev_group] if prev_group else [])

    # Determine best overall across everything
    best_overall = new_best_overall
    if all_images:
        top = max(all_images, key=lambda x: x.get("rating", 0))
        # Only replace if no new best_overall or cached image beats it
        if not best_overall or top.get("rating", 0) > best_overall.get("rating", 0):
            best_overall = {
                "filename": top["filename"],
                "rating": top["rating"],
                "reason": (
                    top.get("description", "")
                    if top["filename"] in cached
                    else best_overall.get("reason", "")
                ),
            }

    return {
        "images": all_images,
        "groups": all_groups,
        "best_overall": best_overall,
    }


# ---------------------------------------------------------------------------
# Rename best picks
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """
    Convert a description string into a safe, lowercase, hyphen-separated filename.

    Examples:
        "Chinesische Mauer im Sonnenuntergang" → "chinesische-mauer-im-sonnenuntergang"
        "Café & Bäume" → "cafe-und-baeume"
    """
    # German umlaut / special character substitutions
    replacements = [
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("Ä", "Ae"),
        ("Ö", "Oe"),
        ("Ü", "Ue"),
        ("ß", "ss"),
        ("&", "und"),
        ("@", "at"),
        ("+", "plus"),
    ]
    for src, dst in replacements:
        text = text.replace(src, dst)

    # Decompose unicode and strip combining characters (accents etc.)
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")

    # Lowercase
    text = text.lower()

    # Replace anything that isn't a letter, digit, or hyphen with a hyphen
    text = re.sub(r"[^a-z0-9]+", "-", text)

    # Collapse multiple hyphens and strip leading/trailing ones
    text = re.sub(r"-{2,}", "-", text).strip("-")

    return text or "bild"


def rename_best_picks(
    day_dir: Path,
    analysis: dict[str, Any],
) -> dict[str, str]:
    """
    Rename web/ JPEG previews for best-pick and best-overall images.

    The new filename is derived from the image's AI description via slugify().
    If two images produce the same slug, a numeric suffix (-2, -3, …) is appended.

    Also renames the corresponding .analysis.json sidecar file.

    Returns a mapping {old_filename: new_filename} for every renamed image.
    """
    web_dir = day_dir / "web"

    # Collect filenames to rename: all best picks + best overall
    to_rename: list[str] = []
    seen: set[str] = set()

    for group in analysis.get("groups", []):
        for fn in group.get("best_picks", []):
            if fn not in seen:
                to_rename.append(fn)
                seen.add(fn)

    best_overall = analysis.get("best_overall", {})
    if best_overall:
        fn = best_overall.get("filename", "")
        if fn and fn not in seen:
            to_rename.append(fn)
            seen.add(fn)

    # Build description lookup from analysis images list
    desc_lookup: dict[str, str] = {
        img["filename"]: img.get("description", "")
        for img in analysis.get("images", [])
        if img.get("filename")
    }

    # Track slugs already assigned in this run to avoid collisions
    used_slugs: dict[str, int] = {}  # slug → next available counter
    renamed: dict[str, str] = {}

    for old_filename in to_rename:
        old_path = web_dir / old_filename
        if not old_path.exists():
            print(
                f"  Warning: Cannot rename {old_filename} — file not found in web/",
                file=sys.stderr,
            )
            continue

        description = desc_lookup.get(old_filename, "")
        if not description:
            # Fall back to original stem if no description available
            description = Path(old_filename).stem

        base_slug = slugify(description)

        # Resolve collision
        if base_slug not in used_slugs:
            slug = base_slug
            used_slugs[base_slug] = 2  # next suffix if another collision occurs
        else:
            suffix = used_slugs[base_slug]
            slug = f"{base_slug}-{suffix}"
            used_slugs[base_slug] = suffix + 1

        new_filename = f"{slug}.jpg"
        new_path = web_dir / new_filename

        # Skip if already has the right name
        if old_path == new_path:
            continue

        # Avoid overwriting an unrelated existing file
        if new_path.exists():
            print(
                f"  Warning: Cannot rename {old_filename} → {new_filename} "
                f"(target already exists), skipping",
                file=sys.stderr,
            )
            continue

        try:
            old_path.rename(new_path)
            renamed[old_filename] = new_filename

            # Rename sidecar too, if it exists
            old_sidecar = web_dir / f"{Path(old_filename).stem}.jpg.analysis.json"
            new_sidecar = web_dir / f"{slug}.jpg.analysis.json"
            if old_sidecar.exists():
                old_sidecar.rename(new_sidecar)

        except Exception as e:
            print(
                f"  Warning: Could not rename {old_filename}: {e}",
                file=sys.stderr,
            )

    return renamed


# ---------------------------------------------------------------------------
# Save analysis results
# ---------------------------------------------------------------------------


def save_sidecar_files(
    day_dir: Path,
    analysis: dict[str, Any],
    model: str,
    provider: str,
) -> None:
    """
    Save individual .analysis.json sidecar files for each image.
    """
    analyzed_at = datetime.now().isoformat()

    # Build lookup: filename -> analysis data
    image_analysis: dict[str, dict[str, Any]] = {}
    for img in analysis.get("images", []):
        filename = img.get("filename", "")
        image_analysis[filename] = {
            "rating": img.get("rating", 0),
            "description": img.get("description", ""),
        }

    # Determine which images are best picks
    best_picks: set[str] = set()
    best_overall_filename: str | None = None

    for group in analysis.get("groups", []):
        for bp in group.get("best_picks", []):
            best_picks.add(bp)

    best_overall = analysis.get("best_overall", {})
    if best_overall:
        best_overall_filename = best_overall.get("filename")

    # Create sidecar files
    for filename, data in image_analysis.items():
        sidecar_path = day_dir / "web" / f"{Path(filename).stem}.jpg.analysis.json"

        sidecar_data = {
            "analyzed_at": analyzed_at,
            "model": model,
            "provider": provider,
            "rating": data["rating"],
            "description": data["description"],
            "groups": [],
            "is_best_pick": filename in best_picks,
            "is_best_overall": filename == best_overall_filename,
        }

        # Find which groups this image belongs to
        for group in analysis.get("groups", []):
            if filename in group.get("image_filenames", []):
                sidecar_data["groups"].append(group.get("name", ""))

        try:
            with open(sidecar_path, "w") as f:
                json.dump(sidecar_data, f, indent=2)
        except Exception as e:
            print(
                f"  Warning: Could not write sidecar for {filename}: {e}",
                file=sys.stderr,
            )


def generate_markdown(
    analysis: dict[str, Any],
    date_str: str,
    day_dir: Path,
    model: str,
    provider: str,
    renamed: dict[str, str] | None = None,
) -> Path:
    """
    Generate markdown summary file in the month folder.

    renamed: optional mapping {old_filename: new_filename} from rename_best_picks().
             When provided, renamed files are shown under their new name.
    """
    if renamed is None:
        renamed = {}

    def display_name(filename: str) -> str:
        """Return the display name for a filename (new name if renamed)."""
        return renamed.get(filename, filename)

    # Parse date to get month folder
    date_parts = date_str.split("/")
    if len(date_parts) != 3:
        raise ValueError(f"Invalid date format: {date_str} (expected YYYY/MM/DD)")

    year, month, day = date_parts
    month_dir = PICTURES_DIR / year / month
    month_dir.mkdir(parents=True, exist_ok=True)

    md_path = month_dir / f"analysis-{year}-{month}-{day}.md"

    # Build markdown content
    lines: list[str] = []

    # Header
    lines.append(f"# Analysis for {date_str}")
    lines.append("")

    # Summary
    images = analysis.get("images", [])
    groups = analysis.get("groups", [])
    best_overall = analysis.get("best_overall", {})

    lines.append("## Summary")
    lines.append(f"- **Total images:** {len(images)}")
    lines.append(f"- **Groups:** {len(groups)}")

    if best_overall:
        rating = best_overall.get("rating", 0)
        filename = best_overall.get("filename", "")
        reason = best_overall.get("reason", "")
        lines.append(
            f"- **Best overall:** {display_name(filename)} ({rating}) - {reason}"
        )

    lines.append(f"- **Model:** {provider}/{model}")
    lines.append("")

    # Groups
    lines.append("## Groups")
    lines.append("")

    for i, group in enumerate(groups, 1):
        name = group.get("name", f"Group {i}")
        description = group.get("description", "")
        time_range = group.get("time_range", "")
        image_filenames = group.get("image_filenames", [])
        best_picks = group.get("best_picks", [])

        # Group header
        time_str = f" ({time_range})" if time_range else ""
        lines.append(f"### Group {i}: {name}{time_str}")
        if description:
            lines.append(f"*{description}*")
        lines.append("")

        # Best picks
        if best_picks:
            lines.append("**Best picks:**")
            for bp_filename in best_picks:
                # Find rating and description
                bp_data = next(
                    (img for img in images if img.get("filename") == bp_filename), {}
                )
                rating = bp_data.get("rating", 0)
                desc = bp_data.get("description", "")
                lines.append(f"- {display_name(bp_filename)} ({rating}) - {desc}")
            lines.append("")

        # All images (sorted by rating)
        lines.append("**All images (by rating):**")

        # Sort group images by rating
        group_images = []
        for fn in image_filenames:
            img_data = next((img for img in images if img.get("filename") == fn), {})
            group_images.append(
                (fn, img_data.get("rating", 0), img_data.get("description", ""))
            )

        group_images.sort(key=lambda x: -x[1])  # Sort by rating descending

        for j, (fn, rating, desc) in enumerate(group_images, 1):
            lines.append(f"{j}. {display_name(fn)} ({rating}) - {desc}")

        lines.append("")

    # Rating distribution
    if images:
        lines.append("## Rating Distribution")

        ratings = [img.get("rating", 0) for img in images]
        excellent = sum(1 for r in ratings if r >= 9.0)
        good = sum(1 for r in ratings if 8.0 <= r < 9.0)
        fair = sum(1 for r in ratings if 7.0 <= r < 8.0)
        poor = sum(1 for r in ratings if r < 7.0)

        lines.append(f"- 9.0+: {excellent} images")
        lines.append(f"- 8.0-8.9: {good} images")
        lines.append(f"- 7.0-7.9: {fair} images")
        lines.append(f"- <7.0: {poor} images")
        lines.append("")

    # Write file
    content = "\n".join(lines)
    with open(md_path, "w") as f:
        f.write(content)

    return md_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze photos in a day folder using AI vision models"
    )
    parser.add_argument(
        "path",
        type=str,
        help="Year/month/day to analyze (e.g. 2025/07/15)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["ollama", "claude"],
        default="ollama",
        help="AI provider to use (default: ollama)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="API key for Claude (required if --provider claude)",
    )
    parser.add_argument(
        "--model",
        type=str,
        help=f"Model name (default: {DEFAULT_OLLAMA_MODEL} for ollama, {DEFAULT_CLAUDE_MODEL} for claude)",
    )
    parser.add_argument(
        "--ollama-base",
        type=str,
        default=DEFAULT_OLLAMA_BASE,
        help=f"Ollama server URL (default: {DEFAULT_OLLAMA_BASE})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Max images to analyze (selects first N by filename)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Images per batch for Ollama (default: 50, adjust based on context window)",
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Re-analyze all images even if sidecar files already exist",
    )
    parser.add_argument(
        "--no-rename",
        action="store_true",
        help="Skip renaming best-pick images to descriptive filenames",
    )
    parser.add_argument(
        "--rename-only",
        action="store_true",
        help="Only rename already-analyzed best-pick images; skip AI analysis",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.provider == "claude" and not args.api_key and not args.rename_only:
        print(
            "Error: --api-key is required when using --provider claude",
            file=sys.stderr,
        )
        sys.exit(1)

    # Set defaults for model
    model = args.model
    if not model:
        model = (
            DEFAULT_CLAUDE_MODEL if args.provider == "claude" else DEFAULT_OLLAMA_MODEL
        )

    # Resolve day directory
    target_dir = PICTURES_DIR / args.path

    if not target_dir.is_dir():
        print(
            f"Error: {target_dir} is not a directory\n"
            f"Expected: YYYY/MM/DD (e.g. 2025/07/15)",
            file=sys.stderr,
        )
        sys.exit(1)

    # --rename-only: load cached sidecars, rename best picks, regenerate markdown
    if args.rename_only:
        web_dir = target_dir / "web"
        print(f"Renaming best picks in: {target_dir}")
        print()

        cached = load_cached_analysis(web_dir)
        if not cached:
            print(
                "Error: No analysis sidecar files found in web/.\n"
                "Run the analysis first before using --rename-only.",
                file=sys.stderr,
            )
            sys.exit(1)

        analysis = merge_with_cached(None, cached)
        renamed = rename_best_picks(target_dir, analysis)

        if renamed:
            print(f"Renamed {len(renamed)} image(s):")
            for old, new in renamed.items():
                print(f"  {old} → {new}")
        else:
            print("Nothing to rename (all best picks already have descriptive names).")

        print()
        print("Regenerating markdown...")
        md_path = generate_markdown(
            analysis, args.path, target_dir, model, args.provider, renamed
        )
        print(f"  Markdown: {md_path.relative_to(PICTURES_DIR)}")
        return

    print(f"Analyzing: {target_dir}")
    print(f"Provider:  {args.provider}")
    print(f"Model:     {model}")
    if args.limit:
        print(f"Limit:     {args.limit} images")
    if args.reanalyze:
        print(f"Mode:      --reanalyze (ignoring existing sidecars)")
    print()

    # Load images
    print("Loading web previews...")
    images = load_web_images(target_dir, args.limit)
    print(f"Loaded {len(images)} images")

    # Check which images are already analyzed (unless --reanalyze)
    web_dir = target_dir / "web"
    cached: dict[str, Any] = {}
    new_images = images

    if not args.reanalyze:
        cached = load_cached_analysis(web_dir)
        if cached:
            new_images = [(fn, data) for fn, data in images if fn not in cached]
            skipped = len(images) - len(new_images)
            if skipped:
                print(f"  Skipping {skipped} already-analyzed image(s)")
            if new_images:
                print(f"  {len(new_images)} new image(s) to analyze")

    print()

    # If nothing new, rebuild markdown from cache and exit
    if not new_images:
        print("All images already analyzed. Regenerating markdown from cache...")
        analysis = merge_with_cached(None, cached)

        # Rename best picks
        renamed: dict[str, str] = {}
        if not args.no_rename:
            renamed = rename_best_picks(target_dir, analysis)

        md_path = generate_markdown(
            analysis, args.path, target_dir, model, args.provider, renamed
        )
        images_count = len(analysis.get("images", []))
        groups_count = len(analysis.get("groups", []))
        best_overall = analysis.get("best_overall", {})
        print()
        print("=" * 50)
        print(f"  Images (cached):  {images_count}")
        print(f"  Groups:           {groups_count}")
        if best_overall:
            print(
                f"  Best overall:     {best_overall.get('filename')} ({best_overall.get('rating')})"
            )
        if renamed:
            print(f"  Renamed:          {len(renamed)} image(s)")
            for old, new in renamed.items():
                print(f"    {old} → {new}")
        print(f"  Markdown:         {md_path.relative_to(PICTURES_DIR)}")
        print("=" * 50)
        return

    # Run analysis on new images only
    print(f"Analyzing {len(new_images)} image(s) with {args.provider}...")
    print(f"  Model: {model}")
    print(f"  This may take a while depending on image count and model speed")
    print()

    date_str = args.path.replace("/", "-")

    if args.provider == "claude":
        new_analysis = analyze_with_claude(
            images=new_images,
            date_str=date_str,
            api_key=args.api_key,  # type: ignore
            model=model,
        )
    else:  # ollama
        new_analysis = analyze_with_ollama(
            images=new_images,
            date_str=date_str,
            model=model,
            base_url=args.ollama_base,
        )

    # Merge new analysis with cached results
    analysis = merge_with_cached(new_analysis, cached)

    # Save results
    print()
    print("Saving analysis...")

    # Save sidecar files for new images only
    save_sidecar_files(target_dir, new_analysis, model, args.provider)

    # Rename best picks to descriptive filenames
    renamed = {}
    if not args.no_rename:
        print("Renaming best picks...")
        renamed = rename_best_picks(target_dir, analysis)

    # Generate markdown
    md_path = generate_markdown(
        analysis, args.path, target_dir, model, args.provider, renamed
    )

    # Print summary
    images_count = len(analysis.get("images", []))
    new_count = len(new_analysis.get("images", []))
    cached_count = images_count - new_count
    groups_count = len(analysis.get("groups", []))
    best_overall = analysis.get("best_overall", {})

    print()
    print("=" * 50)
    print(f"  Images analyzed:  {new_count} new, {cached_count} cached")
    print(f"  Groups found:     {groups_count}")
    if best_overall:
        print(
            f"  Best overall:     {best_overall.get('filename')} ({best_overall.get('rating')})"
        )
    if renamed:
        print(f"  Renamed:          {len(renamed)} image(s)")
        for old, new in renamed.items():
            print(f"    {old} → {new}")
    print(f"  Markdown:         {md_path.relative_to(PICTURES_DIR)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
