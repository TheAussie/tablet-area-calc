#!/usr/bin/env python3
"""
Generate or update tablet_areas.json from a local OpenTabletDriver Configurations folder.

Usage:
    python scripts\\generate_tablet_areas_from_otd.py <path-to-Configurations-folder>

The Configurations folder is the directory that contains vendor subfolders (Wacom, Huion,
XP-Pen, etc.). It is available from the OpenTabletDriver GitHub repository (branch 0.6.x):

    https://github.com/OpenTabletDriver/OpenTabletDriver/tree/0.6.x/OpenTabletDriver.Configurations/Configurations

OTD config fields used:
    Name                                 -> tablet_areas.json key (display/match name)
    Specifications.Digitizer.Width       -> width_mm  (millimeters, no conversion)
    Specifications.Digitizer.Height      -> height_mm (millimeters, no conversion)

Merge behavior:
    - Existing entries with source "opentabletdriver-config" are always refreshed.
    - Existing manual entries (no source field) whose dimensions match OTD are source-tagged.
    - Existing manual entries whose dimensions differ from OTD are preserved unchanged.
    - Use --overwrite-manual to replace all manual entries with OTD data.
    - New entries not already in tablet_areas.json are added and sorted alphabetically.
"""

import argparse
import json
import sys
from pathlib import Path

_SOURCE_TAG = "opentabletdriver-config"
_OUTPUT_FILENAME = "tablet_areas.json"


def _default_output_path():
    return Path(__file__).resolve().parent.parent / _OUTPUT_FILENAME


def _load_existing(output_path):
    if not output_path.is_file():
        return {}
    try:
        with output_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        print(
            f"Warning: {output_path} root is not a JSON object; treating as empty.",
            file=sys.stderr,
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"Warning: could not read existing {output_path}: {exc}; starting fresh.",
            file=sys.stderr,
        )
    return {}


def _extract_entry(config_path):
    """Return ((name, width_mm, height_mm), None) on success, or (None, reason) on failure."""
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except OSError as exc:
        return None, f"read error: {exc}"

    if not isinstance(data, dict):
        return None, "root is not a JSON object"

    name = data.get("Name")
    if not isinstance(name, str) or not name.strip():
        return None, "missing or empty Name field"

    specs = data.get("Specifications")
    if not isinstance(specs, dict):
        return None, "missing Specifications"

    digitizer = specs.get("Digitizer")
    if not isinstance(digitizer, dict):
        return None, "missing Specifications.Digitizer"

    raw_w = digitizer.get("Width")
    raw_h = digitizer.get("Height")
    try:
        width = float(raw_w)
        height = float(raw_h)
    except (TypeError, ValueError):
        return None, f"Width/Height are not valid numbers ({raw_w!r}, {raw_h!r})"

    if width <= 0 or height <= 0:
        return None, f"non-positive dimensions: {width} x {height}"

    return (name.strip(), width, height), None


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate or update tablet_areas.json from a local OTD Configurations folder. "
            "Manual entries are preserved by default when their dimensions differ from OTD data."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python scripts\\generate_tablet_areas_from_otd.py "
            '"C:\\OpenTabletDriver\\OpenTabletDriver.Configurations\\Configurations"'
        ),
    )
    parser.add_argument(
        "configs_dir",
        help=(
            "Path to the OTD Configurations folder "
            "(the directory containing vendor subfolders such as Wacom, Huion, XP-Pen, etc.)"
        ),
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help=f"Output path for tablet_areas.json (default: project root {_OUTPUT_FILENAME})",
    )
    parser.add_argument(
        "--overwrite-manual",
        action="store_true",
        help=(
            "Replace manual entries (those without a source field) even when "
            "their dimensions differ from OTD data"
        ),
    )
    args = parser.parse_args()

    configs_dir = Path(args.configs_dir)
    if not configs_dir.exists():
        print(f"Error: path does not exist: {configs_dir}", file=sys.stderr)
        sys.exit(1)
    if not configs_dir.is_dir():
        print(f"Error: not a directory: {configs_dir}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output).resolve() if args.output else _default_output_path()
    existing = _load_existing(output_path)

    scanned = 0
    valid_entries = []
    skipped = []

    for config_file in sorted(configs_dir.rglob("*.json")):
        scanned += 1
        result, reason = _extract_entry(config_file)
        if result is None:
            skipped.append((config_file, reason))
        else:
            valid_entries.append(result)

    final = dict(existing)
    n_added = 0
    n_updated = 0
    n_preserved = 0

    for name, width, height in valid_entries:
        new_entry = {"width_mm": width, "height_mm": height, "source": _SOURCE_TAG}

        if name not in final:
            final[name] = new_entry
            n_added += 1
            continue

        existing_entry = final[name]
        was_generated = existing_entry.get("source") == _SOURCE_TAG

        if was_generated or args.overwrite_manual:
            final[name] = new_entry
            n_updated += 1
            continue

        # Manual entry: add source tag only when dimensions already match OTD.
        try:
            dims_match = (
                float(existing_entry.get("width_mm")) == width
                and float(existing_entry.get("height_mm")) == height
            )
        except (TypeError, ValueError):
            dims_match = False

        if dims_match:
            final[name] = {**existing_entry, "source": _SOURCE_TAG}
            n_updated += 1
        else:
            n_preserved += 1

    # Existing entries keep their original order; new entries are appended alphabetically.
    existing_keys = list(existing)
    new_keys = sorted(k for k in final if k not in existing)
    ordered = {k: final[k] for k in existing_keys if k in final}
    for k in new_keys:
        ordered[k] = final[k]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2)
        f.write("\n")

    print(f"OTD folder: {configs_dir}")
    print(f"Output:     {output_path}")
    print(f"Scanned:    {scanned} JSON file(s)")
    print(f"Valid:      {len(valid_entries)} entries extracted from OTD configs")
    print(f"Added:      {n_added} new")
    print(f"Updated:    {n_updated} refreshed or source-tagged")
    print(f"Preserved:  {n_preserved} manual entries kept (dimensions differ from OTD)")
    print(f"Skipped:    {len(skipped)} file(s) — no usable digitizer dimensions")
    print(f"Total:      {len(ordered)} entries written to {_OUTPUT_FILENAME}")

    if skipped:
        limit = 20
        print(f"\nSkipped ({min(len(skipped), limit)} of {len(skipped)} shown):")
        for config_file, reason in skipped[:limit]:
            try:
                rel = config_file.relative_to(configs_dir)
            except ValueError:
                rel = config_file
            print(f"  {rel}: {reason}")
        if len(skipped) > limit:
            print(f"  ... and {len(skipped) - limit} more")


if __name__ == "__main__":
    main()
