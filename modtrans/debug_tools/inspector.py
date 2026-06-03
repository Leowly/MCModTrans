"""Inspect a single JAR file in detail.

Usage::

    modtrans inspect path/to/mod.jar
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..parser.jar_parser import JarParseError, JarParser


def inspect_jar(jar_path: Path) -> dict[str, Any]:
    """Deep-dive into a single mod JAR and return detailed information.

    Args:
        jar_path: Path to a .jar mod file.

    Returns:
        Detailed inspection result as a dictionary.
    """
    parser = JarParser()

    try:
        assets = parser.parse_jar(jar_path)
    except JarParseError as e:
        return {
            "jar": jar_path.name,
            "error": str(e),
            "parseable": False,
        }

    # Classify existing Chinese entries
    translated = []
    english_remaining = []
    zh_only = []  # in zh_cn but not en_us

    for key, zh_value in assets.existing_chinese.items():
        en_value = assets.english_entries.get(key)
        if en_value is None:
            zh_only.append({"key": key, "zh_value": zh_value})
        elif zh_value.strip() == en_value.strip():
            english_remaining.append({"key": key, "en_value": en_value})
        else:
            translated.append({"key": key, "en": en_value, "zh": zh_value})

    # Sample entries (first 20)
    sample_en = dict(list(assets.english_entries.items())[:20])
    sample_zh = dict(list(assets.existing_chinese.items())[:20])

    return {
        "jar": jar_path.name,
        "jar_size_mb": round(jar_path.stat().st_size / (1024 * 1024), 2),
        "parseable": True,
        "modid": assets.modid,
        "game_version_format": assets.game_version.value,
        "source_encoding": assets.source_encoding,
        "metadata": {
            "name": assets.metadata.name,
            "version": assets.metadata.version,
            "author": assets.metadata.author,
            "game_version": assets.metadata.game_version,
            "description": assets.metadata.description[:200] if assets.metadata.description else "",
        },
        "stats": {
            "total_en_entries": len(assets.english_entries),
            "total_zh_entries": len(assets.existing_chinese),
            "properly_translated": len(translated),
            "english_remaining_in_zh": len(english_remaining),
            "zh_only_keys": len(zh_only),
        },
        "sample_en_entries": sample_en,
        "sample_zh_entries": sample_zh,
        "english_remaining_samples": english_remaining[:10],
        "zh_only_samples": zh_only[:10],
    }


def print_inspection(result: dict[str, Any]) -> None:
    """Print a detailed inspection report to the console."""
    if not result.get("parseable"):
        print(f"\n  错误: {result.get('error', '未知错误')}\n")
        return

    meta = result["metadata"]
    stats = result["stats"]

    print()
    print("=" * 68)
    print(f"  JAR 详细信息: {result['jar']}")
    print("=" * 68)
    print(f"  文件大小:     {result['jar_size_mb']} MB")
    print(f"  Mod ID:       {result['modid']}")
    print(f"  格式:         {result['game_version_format']}")
    print(f"  编码:         {result['source_encoding']}")
    print(f"  名称:         {meta['name'] or '-'}")
    print(f"  作者:         {meta['author'] or '-'}")
    print(f"  MC 版本:      {meta['game_version'] or '-'}")
    print("-" * 68)
    print(f"  英文条目:     {stats['total_en_entries']}")
    print(f"  中文条目:     {stats['total_zh_entries']}")
    print(f"  已翻译:       {stats['properly_translated']}")
    print(f"  中文残留英文: {stats['english_remaining_in_zh']}")
    print(f"  仅中文键:     {stats['zh_only_keys']}")
    print("-" * 68)

    # Show English remaining in zh_cn
    if result.get("english_remaining_samples"):
        print("\n  zh_cn 中残留的英文文本 (前10条):")
        for item in result["english_remaining_samples"]:
            print(f"    {item['key']}")
            print(f"      → \"{item['en_value'][:120]}\"")
        print()

    # Show sample en entries
    if result.get("sample_en_entries"):
        print("  en_us 条目示例:")
        for key, value in list(result["sample_en_entries"].items())[:10]:
            print(f"    {key} = {value[:100]}")
        print()

    # Show zh-only keys (keys in zh_cn but not en_us)
    if result.get("zh_only_samples"):
        print("  zh_cn 中有但 en_us 中不存在的键 (可能已废弃):")
        for item in result["zh_only_samples"]:
            print(f"    {item['key']} = {item['zh_value'][:100]}")
        print()

    print("=" * 68)
    print()
