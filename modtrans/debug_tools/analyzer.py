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
        logger.warning("未找到 JAR 文件: %s", mods_dir)
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
            logger.warning("跳过 %s: %s", jar_path.name, e)
            failed_count += 1
            mods.append({
                "jar": jar_path.name,
                "modid": "-",
                "name": "-",
                "author": "-",
                "game_version": "-",
                "format": "no lang",
                "en_keys": 0,
                "zh_keys": 0,
                "zh_coverage_pct": "-",
                "zh_missing": "-",
                "english_in_zh": "-",
                "encoding": "-",
                "error": str(e),
            })

    return {
        "total_jars": len(jar_paths),
        "parsed": len(jar_paths) - failed_count,
        "no_lang": failed_count,
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


def _fmt(val: Any, suffix: str = "") -> str:
    """Format a table cell value, returning '-' for non-numeric placeholders."""
    if isinstance(val, (int, float)):
        return f"{val}{suffix}"
    return f"{val}"


def print_analysis(report: dict[str, Any]) -> None:
    """Print an analysis report to the console in a readable format."""
    print()
    print("=" * 72)
    print("  MC Mod 翻译分析报告")
    print("=" * 72)
    print(f"  JAR 总数:       {report['total_jars']:>4}")
    print(f"  含有语言文件:   {report['parsed']:>4}")
    print(f"  无语言文件:     {report['no_lang']:>4}  (工具库/前置模组，无需翻译)")
    print(f"  旧版 (.lang):   {report['legacy_format']:>4}")
    print(f"  新版 (.json):   {report['modern_format']:>4}")
    print(f"  英文条目总数:   {report['total_en_keys']:>6}")
    print(f"  中文条目总数:   {report['total_zh_keys']:>6}")
    print(f"  汉化覆盖率:     {report['overall_zh_coverage_pct']:>5.1f}%")
    print("-" * 72)
    print(f"  {'Mod':<40} {'英文':>5} {'中文':>5} {'覆盖%':>7} {'缺失':>5} {'英在中':>7}")
    print("-" * 72)

    for mod in report["mods"]:
        name = mod["jar"][:38] + (".." if len(mod["jar"]) > 40 else "")
        en = _fmt(mod["en_keys"])
        zh = _fmt(mod["zh_keys"])
        cov = _fmt(mod["zh_coverage_pct"], suffix="%") if isinstance(mod["zh_coverage_pct"], (int, float)) else "      -"
        miss = _fmt(mod["zh_missing"])
        eng_in = _fmt(mod["english_in_zh"])
        print(f"  {name:<40} {en:>5} {zh:>5} {cov:>7} {miss:>5} {eng_in:>7}")

    print("-" * 72)
    print(f"  {'合计':<40} {report['total_en_keys']:>5} {report['total_zh_keys']:>5}")
    print("=" * 72)
    print()

    # Show mods needing full translation (only those with actual text)
    untranslated = [
        m for m in report["mods"]
        if m["zh_coverage_pct"] == 0 and m["en_keys"] > 0
    ]
    if untranslated:
        print(f"  完全没有汉化的 Mod ({len(untranslated)}):")
        for m in untranslated:
            print(f"    - {m['jar']}  ({m['en_keys']} 条)")
        print()

    # Show mods with English left in zh_cn
    eng_in_zh = [m for m in report["mods"] if isinstance(m["english_in_zh"], int) and m["english_in_zh"] > 0]
    if eng_in_zh:
        print(f"  zh_cn 中残留英文的 Mod ({len(eng_in_zh)}):")
        for m in eng_in_zh:
            print(
                f"    - {m['jar']}  ({m['english_in_zh']} 条仍为英文)"
            )
        print()
