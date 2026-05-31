"""查找 mod JAR 中缺少英文显示名称的物品/方块。

在 Minecraft 中，如果物品/方块没有对应的语言条目，游戏会直接显示
原始 ID（如 ``item.modid.redstone_sword.name``），影响游玩体验。
此工具扫描 JAR 中的模型文件，比对语言条目，找出这些"未命名"物品。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from ..parser.encoding import decode_lang
from ..parser.lang_parser import parse_lang
from ..parser.json_parser import parse_json

logger = logging.getLogger(__name__)


def find_untagged(mods_dir: Path) -> dict[str, Any]:
    """扫描所有 mod JAR，找出缺少英文显示名称的物品/方块。

    策略：
    1. 从 models/item/*.json 和 blockstates/*.json 提取物品/方块名称
    2. 从 en_us.* 语言文件中收集所有已知翻译键
    3. 按常见键命名模式匹配，找出没有对应 lang key 的物品
    4. 对于根本没有语言文件的 mod，列出所有模型物品（全部未命名）

    Args:
        mods_dir: mods 文件夹路径。

    Returns:
        包含总览统计和每个 mod 发现结果的字典。
    """
    jar_paths = sorted(mods_dir.glob("*.jar"))
    results: dict[str, Any] = {
        "total_jars": len(jar_paths),
        "findings": [],
        "summary": {
            "no_lang_mods": 0,
            "total_untagged_items": 0,
            "total_untagged_blocks": 0,
        },
    }

    for jar_path in jar_paths:
        try:
            finding = _analyze_single_jar(jar_path)
            if finding:
                results["findings"].append(finding)
                if finding.get("no_lang_file"):
                    results["summary"]["no_lang_mods"] += 1
                results["summary"]["total_untagged_items"] += finding.get(
                    "untagged_items_count", 0
                )
                results["summary"]["total_untagged_blocks"] += finding.get(
                    "untagged_blocks_count", 0
                )
        except Exception as e:
            logger.warning("无法分析 %s: %s", jar_path.name, e)

    return results


def _analyze_single_jar(jar_path: Path) -> dict[str, Any] | None:
    """分析单个 JAR 中未命名的物品/方块。"""
    with ZipFile(jar_path, "r") as zf:
        names = zf.namelist()

        # 1. 收集模型物品和方块名称
        model_items: set[str] = set()  # 来自 models/item/
        model_blocks: set[str] = set()  # 来自 blockstates/

        for name in names:
            if "models/item/" in name and name.endswith(".json"):
                model_items.add(_stem(name))
            elif "blockstates/" in name and name.endswith(".json"):
                model_blocks.add(_stem(name))

        if not model_items and not model_blocks:
            return None  # 没有任何模型文件，无可检测

        # 2. 推断 modid
        modid = _infer_modid_from_zip(names)

        # 3. 收集语言文件
        lang_files = [n for n in names if "/lang/" in n and n.endswith((".lang", ".json"))]
        en_us_files = [f for f in lang_files if _is_en_us(f)]
        zh_cn_files = [f for f in lang_files if _is_zh_cn(f)]

        no_lang_file = not en_us_files

        # 4. 解析已有的 en_us 键
        known_keys: set[str] = set()
        if en_us_files:
            for path in en_us_files:
                try:
                    raw = zf.read(path)
                    if path.endswith(".lang"):
                        text, _ = decode_lang(raw)
                        known_keys.update(parse_lang(text).keys())
                    else:
                        known_keys.update(parse_json(raw).keys())
                except Exception:
                    pass

        # 5. 检查每个模型物品是否有对应的 lang key
        untagged_items: list[str] = []
        untagged_blocks: list[str] = []
        item_matches: dict[str, list[str]] = {}  # item_name → matched keys (for debug)
        block_matches: dict[str, list[str]] = {}

        for item_name in sorted(model_items):
            matched = _find_lang_keys(item_name, modid, known_keys)
            item_matches[item_name] = matched
            if not matched:
                untagged_items.append(item_name)

        for block_name in sorted(model_blocks):
            matched = _find_lang_keys(block_name, modid, known_keys)
            block_matches[block_name] = matched
            if not matched:
                untagged_blocks.append(block_name)

        return {
            "jar": jar_path.name,
            "modid": modid,
            "no_lang_file": no_lang_file,
            "lang_files": [n.split("/")[-1] for n in lang_files],
            "has_en_us": bool(en_us_files),
            "has_zh_cn": bool(zh_cn_files),
            "model_items_count": len(model_items),
            "model_blocks_count": len(model_blocks),
            "known_lang_keys": len(known_keys),
            "untagged_items": untagged_items,
            "untagged_items_count": len(untagged_items),
            "untagged_blocks": untagged_blocks,
            "untagged_blocks_count": len(untagged_blocks),
            # 对于无语言文件的 mod，建议生成的 lang 键
            "suggested_keys": (
                _suggest_keys(untagged_items, untagged_blocks, modid)
                if no_lang_file and (untagged_items or untagged_blocks)
                else {}
            ),
        }


def _find_lang_keys(name: str, modid: str, known_keys: set[str]) -> list[str]:
    """检查一个物品/方块名称是否有对应的语言键。

    尝试的命名模式（按优先级）：
    1. ``item.<modid>.<name>.name`` — Forge 物品标准格式
    2. ``tile.<modid>.<name>.name`` — Forge 方块标准格式
    3. ``block.<modid>.<name>.name`` — 部分 mod 使用的方块格式
    4. ``<modid>.<name>.name`` — 简短格式
    5. ``item.<name>.name`` — 无 modid 前缀
    6. ``tile.<name>.name`` — 无 modid 方块前缀
    7. 模糊匹配：任何以 ``.<name>.name`` 结尾的键

    Returns:
        匹配到的所有 lang key 列表。
    """
    candidates = [
        f"item.{modid}.{name}.name",
        f"tile.{modid}.{name}.name",
        f"block.{modid}.{name}.name",
        f"{modid}.{name}.name",
        f"item.{name}.name",
        f"tile.{name}.name",
    ]

    matched = [c for c in candidates if c in known_keys]

    # 模糊匹配：检查是否任何已知键以 .<name>.name 结尾
    if not matched:
        suffix = f".{name}.name"
        fuzzy = [k for k in known_keys if k.endswith(suffix)]
        matched.extend(fuzzy)

    return matched


def _suggest_keys(
    items: list[str],
    blocks: list[str],
    modid: str,
) -> dict[str, list[str]]:
    """为没有语言文件的 mod 建议生成的语言键。

    Returns:
        {"items": [...], "blocks": [...]} — 建议的完整 lang key 列表。
    """
    return {
        "items": [f"item.{modid}.{item}.name" for item in items],
        "blocks": [f"tile.{modid}.{block}.name" for block in blocks],
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

_ITEM_NAME_CLEANUP = re.compile(r"[^a-zA-Z0-9_]+")


def _name_to_english(name: str) -> str:
    """将物品的内部名称转为可读英文。

    ``redstone_sword`` → "Redstone Sword"
    ``copper_furnace`` → "Copper Furnace"
    ``item.mythril_ingot`` → "Mythril Ingot"
    """
    cleaned = _ITEM_NAME_CLEANUP.sub(" ", name)
    return " ".join(w.capitalize() for w in cleaned.split())


def _stem(path: str) -> str:
    """获取路径中文件名的 stem（不含扩展名）。"""
    return Path(path).stem


def _is_en_us(path: str) -> bool:
    """判断一个语言文件路径是否为 en_us。"""
    filename = Path(path).name.lower()
    return "en_us" in filename or "en-us" in filename


def _is_zh_cn(path: str) -> bool:
    """判断一个语言文件路径是否为 zh_cn。"""
    filename = Path(path).name.lower()
    return "zh_cn" in filename or "zh-cn" in filename


def _infer_modid_from_zip(names: list[str]) -> str:
    """从 ZIP 文件列表中推断 modid。

    策略：
    1. 从 assets/<modid>/ 目录结构
    2. 从 mcmod.info
    3. 从语言文件路径
    """
    # 策略 1: assets/<modid>/
    for name in names:
        parts = name.split("/")
        if len(parts) >= 2 and parts[0] == "assets":
            candidate = parts[1]
            if candidate and candidate != "lang":
                return candidate

    # 策略 2: 从语言文件路径
    for name in names:
        if "/lang/" in name:
            parts = name.split("/")
            for i, p in enumerate(parts):
                if p == "assets" and i + 2 < len(parts) and parts[i + 2] == "lang":
                    return parts[i + 1]

    return "unknown"


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def print_findings(results: dict[str, Any]) -> None:
    """打印未命名物品/方块检测报告。"""
    summary = results.get("summary", {})
    findings = results.get("findings", [])

    print()
    print("=" * 72)
    print("  未命名物品/方块检测报告")
    print("=" * 72)
    print(f"  扫描 JAR 总数:       {results.get('total_jars', 0)}")
    print(f"  无语言文件的 Mod:    {summary.get('no_lang_mods', 0)}")
    print(f"  未命名物品总数:      {summary.get('total_untagged_items', 0)}")
    print(f"  未命名方块总数:      {summary.get('total_untagged_blocks', 0)}")
    print("=" * 72)

    # 先列出完全没有语言文件的 mod（最严重的情况）
    no_lang_findings = [f for f in findings if f.get("no_lang_file")]
    if no_lang_findings:
        print(f"\n  【完全没有语言文件的 Mod】({len(no_lang_findings)} 个)")
        print("  " + "-" * 68)
        for f in no_lang_findings:
            print(f"\n  ◆ {f['jar']}")
            print(f"    Mod ID:      {f['modid']}")
            unt_items = f.get("untagged_items", [])
            unt_blocks = f.get("untagged_blocks", [])
            print(f"    未命名物品:  {len(unt_items)} 个")
            if unt_items:
                print(f"      示例:      {', '.join(unt_items[:8])}")
            print(f"    未命名方块:  {len(unt_blocks)} 个")
            if unt_blocks:
                print(f"      示例:      {', '.join(unt_blocks[:8])}")
            # 显示建议的 lang key 和自动生成的英文名
            suggested = f.get("suggested_keys", {})
            if suggested:
                sug_items = suggested.get("items", [])[:5]
                if sug_items:
                    print(f"    建议生成键 (物品):")
                    for k in sug_items:
                        stem = k.rsplit(".", 2)[0].split(".")[-1] if "." in k else k
                        print(f"      {k} → \"{_name_to_english(stem)}\"")
                sug_blocks = suggested.get("blocks", [])[:3]
                if sug_blocks:
                    print(f"    建议生成键 (方块):")
                    for k in sug_blocks:
                        stem = k.rsplit(".", 2)[0].split(".")[-1] if "." in k else k
                        print(f"      {k} → \"{_name_to_english(stem)}\"")

    # 有语言文件但仍存在未命名物品的 mod
    partial_findings = [
        f for f in findings
        if not f.get("no_lang_file")
        and (f.get("untagged_items") or f.get("untagged_blocks"))
    ]
    if partial_findings:
        print(f"\n  【有语言文件但仍有未命名物品的 Mod】({len(partial_findings)} 个)")
        print("  " + "-" * 68)
        for f in partial_findings:
            unt_items = f.get("untagged_items", [])
            unt_blocks = f.get("untagged_blocks", [])
            print(f"\n  ◇ {f['jar']}")
            print(f"    Mod ID:      {f['modid']}")
            print(f"    语言文件:    {', '.join(f['lang_files'])}")
            print(f"    已有语言键:  {f.get('known_lang_keys', 0)}")
            if unt_items:
                print(f"    ❌ 未命名物品 ({len(unt_items)}): {', '.join(unt_items[:10])}")
            if unt_blocks:
                print(f"    ❌ 未命名方块 ({len(unt_blocks)}): {', '.join(unt_blocks[:10])}")

    # 全部正常的 mod
    clean_count = sum(
        1 for f in findings
        if not f.get("untagged_items") and not f.get("untagged_blocks")
        and not f.get("no_lang_file")
    )
    if clean_count > 0:
        print(f"\n  ✅ 完全正常的 Mod: {clean_count} 个")
        print(f"     (所有模型物品/方块都有对应的英文名)")

    # 没有可检测内容的 mod
    no_model_count = results.get("total_jars", 0) - len(findings)
    if no_model_count > 0:
        print(f"\n  ℹ 无可检测内容的 JAR: {no_model_count} 个")
        print(f"     (无 models/item/ 或 blockstates/ 目录)")

    print("\n" + "=" * 72)
    print("  提示: 对于没有语言文件的 Mod，翻译工具可以自动生成英文名并翻译。")
    print("  使用 `modtrans translate --generate-untagged` 启用此功能。")
    print("=" * 72)
    print()
