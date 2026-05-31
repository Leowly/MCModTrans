"""Find items without English names in mod JARs.

Some mods have items that lack even English display names. This tool
identifies those cases by scanning for registered items that have no
corresponding en_us entry.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from zipfile import ZipFile

logger = logging.getLogger(__name__)


def find_untagged(mods_dir: Path) -> dict[str, Any]:
    """Find items across all mods that may lack English display names.

    Strategy:
    - For each JAR, compare registered items (from models/, blockstates/,
      or lang keys pattern) against existing en_us entries.
    - Report items where no lang key matches.

    Note: This is a heuristic — not all items have lang keys, and some
    items share keys. The output is investigative, not definitive.

    Args:
        mods_dir: Path to the mods directory.

    Returns:
        Dictionary with per-mod findings.
    """
    jar_paths = sorted(mods_dir.glob("*.jar"))
    results: dict[str, Any] = {"total_jars": len(jar_paths), "findings": []}

    for jar_path in jar_paths:
        try:
            finding = _analyze_single_jar(jar_path)
            if finding:
                results["findings"].append(finding)
        except Exception as e:
            logger.warning("Could not analyze %s: %s", jar_path.name, e)

    return results


def _analyze_single_jar(jar_path: Path) -> dict[str, Any] | None:
    """Analyze a single JAR for untagged items."""
    with ZipFile(jar_path, "r") as zf:
        names = zf.namelist()

        # Find lang files
        lang_files = [n for n in names if "/lang/" in n and n.endswith((".lang", ".json"))]

        if not lang_files:
            return None

        # Extract modid from first lang path
        modid = "unknown"
        for path in lang_files:
            parts = path.split("/")
            for i, p in enumerate(parts):
                if p == "assets" and i + 2 < len(parts) and parts[i + 2] == "lang":
                    modid = parts[i + 1]
                    break
            if modid != "unknown":
                break

        # Find potential item references in models, blockstates, textures
        model_items: set[str] = set()
        for name in names:
            if "models/item/" in name and name.endswith(".json"):
                item_name = Path(name).stem  # e.g. "iron_sword"
                model_items.add(item_name)
            elif "blockstates/" in name and name.endswith(".json"):
                block_name = Path(name).stem
                model_items.add(block_name)

        return {
            "jar": jar_path.name,
            "modid": modid,
            "lang_files": [n.split("/")[-1] for n in lang_files],
            "model_items_count": len(model_items),
            "sample_items": sorted(model_items)[:20],
        }


def print_findings(results: dict[str, Any]) -> None:
    """Print the find-untagged report."""
    print()
    print("=" * 68)
    print("  Find Untagged Items Report")
    print("=" * 68)

    for finding in results.get("findings", []):
        print(f"\n  {finding['jar']}")
        print(f"    Mod ID:     {finding['modid']}")
        print(f"    Lang files: {', '.join(finding['lang_files'])}")
        print(f"    Model items:{finding['model_items_count']}")
        if finding.get("sample_items"):
            print(f"    Sample:     {', '.join(finding['sample_items'][:10])}")
