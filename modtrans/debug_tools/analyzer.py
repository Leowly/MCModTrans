"""Analyze mods structure — summarize language file coverage across all JARs.

Usage::

    modtrans analyze -m ./mods
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..models import GameVersion, ModAssets
from ..parser.jar_parser import JarParser, JarParseError

logger = logging.getLogger(__name__)


def analyze_mods(mods_dir: Path) -> dict[str, Any]:
    """Analyze all JARs in a mods directory and return a summary.

    Args:
        mods_dir: Path to the directory containing .jar mod files.

    Returns:
        A dictionary with summary statistics and per-mod details.
    """
    jar_paths = sorted(mods_dir.glob("*.jar"))
    if not jar_paths:
        logger.warning("No JAR files found in %s", mods_dir)
        return {"total_jars": 0, "mods": []}

    parser = JarParser()
    mods: list[dict[str, Any]] = []
    total_en_keys = 0
    total_zh_keys = 0
    legacy_count = 0
    modern_count = 0
    unknown_count = 0
    failed_count = 0

    for jar_path in jar_paths:
        try:
            assets = parser.parse_jar(jar_path)
            en_count = len(assets.english_entries)
            zh_count = len(assets.existing_chinese)
            zh_coverage = (zh_count / en_count * 100) if en_count > 0 else 0
            zh_missing = en_count - zh_count

            total_en_keys += en_count
            total_zh_keys += zh_count

            if assets.game_version == GameVersion.LEGACY:
                legacy_count += 1
            elif assets.game_version == GameVersion.MODERN:
                modern_count += 1
            else:
                unknown_count += 1

            # Count entries where zh_cn value equals en_us (English not translated)
            english_still_in_zh = sum(
                1
                for k, v in assets.existing_chinese.items()
                if k in assets.english_entries
                and v.strip() == assets.english_entries[k].strip()
            )

            mods.append({
                "jar": jar_path.name,
                "modid": assets.modid,
                "name": assets.metadata.name or "-",
                "author": assets.metadata.author or "-",
                "game_version": assets.metadata.game_version or assets.game_version.value,
                "format": assets.game_version.value,
                "en_keys": en_count,
                "zh_keys": zh_count,
                "zh_coverage_pct": round(zh_coverage, 1),
                "zh_missing": zh_missing,
                "english_in_zh": english_still_in_zh,
                "encoding": assets.source_encoding,
            })
        except JarParseError as e:
            logger.warning("Skipping %s: %s", jar_path.name, e)
            failed_count += 1
            mods.append({
                "jar": jar_path.name,
                "modid": "-",
                "name": "-",
                "author": "-",
                "game_version": "-",
                "format": "FAILED",
                "en_keys": 0,
                "zh_keys": 0,
                "zh_coverage_pct": 0,
                "zh_missing": 0,
                "english_in_zh": 0,
                "encoding": "-",
                "error": str(e),
            })

    return {
        "total_jars": len(jar_paths),
        "parsed": len(jar_paths) - failed_count,
        "failed": failed_count,
        "legacy_format": legacy_count,
        "modern_format": modern_count,
        "unknown_format": unknown_count,
        "total_en_keys": total_en_keys,
        "total_zh_keys": total_zh_keys,
        "overall_zh_coverage_pct": (
            round(total_zh_keys / total_en_keys * 100, 1)
            if total_en_keys > 0
            else 0
        ),
        "mods": mods,
    }


def print_analysis(report: dict[str, Any]) -> None:
    """Print an analysis report to the console in a readable format."""
    print()
    print("=" * 72)
    print("  MC Mod Translation Analysis")
    print("=" * 72)
    print(f"  Total JARs:    {report['total_jars']:>4}")
    print(f"  Parsed OK:     {report['parsed']:>4}")
    print(f"  Failed:        {report['failed']:>4}")
    print(f"  Legacy (.lang): {report['legacy_format']:>4}")
    print(f"  Modern (.json): {report['modern_format']:>4}")
    print(f"  Total EN keys:  {report['total_en_keys']:>6}")
    print(f"  Total ZH keys:  {report['total_zh_keys']:>6}")
    print(f"  ZH Coverage:    {report['overall_zh_coverage_pct']:>5.1f}%")
    print("-" * 72)
    print(f"  {'Mod':<40} {'EN':>5} {'ZH':>5} {'Cover%':>7} {'Miss':>5} {'EngInZH':>7}")
    print("-" * 72)

    for mod in report["mods"]:
        name = mod["jar"][:38] + (".." if len(mod["jar"]) > 40 else "")
        print(
            f"  {name:<40} "
            f"{mod['en_keys']:>5} "
            f"{mod['zh_keys']:>5} "
            f"{mod['zh_coverage_pct']:>6.1f}% "
            f"{mod['zh_missing']:>5} "
            f"{mod['english_in_zh']:>7}"
        )

    print("-" * 72)
    print(f"  {'TOTAL':<40} {report['total_en_keys']:>5} {report['total_zh_keys']:>5}")
    print("=" * 72)
    print()

    # Show mods needing full translation
    untranslated = [m for m in report["mods"] if m["zh_coverage_pct"] == 0]
    if untranslated:
        print(f"  Mods with NO Chinese translation ({len(untranslated)}):")
        for m in untranslated:
            print(f"    - {m['jar']}  ({m['en_keys']} keys)")
        print()

    # Show mods with English left in zh_cn
    eng_in_zh = [m for m in report["mods"] if m["english_in_zh"] > 0]
    if eng_in_zh:
        print(f"  Mods with English text still in zh_cn ({len(eng_in_zh)}):")
        for m in eng_in_zh:
            print(
                f"    - {m['jar']}  ({m['english_in_zh']} entries are still English)"
            )
        print()
