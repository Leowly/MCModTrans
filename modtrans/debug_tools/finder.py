"""查找 mod JAR 中缺少英文显示名称的物品/方块。

在 Minecraft 中，如果物品/方块没有对应的语言条目，游戏会直接显示
原始 ID（如 ``item.modid.redstone_sword.name``），影响游玩体验。

此工具使用共享的 ``model_scanner`` 模块进行检测（与 translate/analyze 一致），
确保所有命令的统计结果完全统一。

委托流程:
1. 打开 JAR 轻量扫描模型文件列表
2. 通过 JarParser 获取正确的 modid 和已知语言键
3. 通过 model_scanner 检测未命名物品/方块
4. 无语言文件的 mod 回退到路径推断 + scan_jar_direct()
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from ..parser.jar_parser import JarParser, JarParseError

logger = logging.getLogger(__name__)


def find_untagged(mods_dir: Path) -> dict[str, Any]:
    """扫描所有 mod JAR，找出缺少英文显示名称的物品/方块。

    使用与 translate/analyze 相同的 model_scanner 逻辑。

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
    """分析单个 JAR 中未命名的物品/方块。

    1. 轻量扫描模型文件名（不解析 JSON 内容）
    2. 通过 JarParser 获取 modid + 已知语言键
    3. 通过 model_scanner 执行未命名检测
    """
    from ..analyzer.model_scanner import scan_mod, scan_jar_direct

    # 1. 轻量扫描 JAR 获取模型文件和语言文件名
    with ZipFile(jar_path, "r") as zf:
        names = zf.namelist()

        model_items: set[str] = set()
        model_blocks: set[str] = set()
        for name in names:
            if "models/item/" in name and name.endswith(".json"):
                model_items.add(Path(name).stem)
            elif "blockstates/" in name and name.endswith(".json"):
                model_blocks.add(Path(name).stem)

        if not model_items and not model_blocks:
            return None  # 没有任何模型文件，无可检测

        lang_file_names = [
            n.split("/")[-1] for n in names
            if "/lang/" in n and n.endswith((".lang", ".json"))
        ]

    # 2. 尝试用 JarParser 完整解析
    modid = "unknown"
    result_untagged = None
    no_lang_file = True

    try:
        parser = JarParser()
        assets = parser.parse_jar(jar_path)
        modid = assets.modid
        no_lang_file = not _has_any_en_us(lang_file_names)
        result_untagged = scan_mod(assets)
    except JarParseError:
        # 无语言文件 — 从路径推断 modid
        with ZipFile(jar_path, "r") as zf:
            modid = _infer_modid_from_zip(zf)
        result_untagged = scan_jar_direct(jar_path, modid, set())

    # 3. 构造返回字典
    en_us_files = [f for f in lang_file_names if _is_en_us(f)]
    zh_cn_files = [f for f in lang_file_names if _is_zh_cn(f)]

    untagged_items = result_untagged.untagged_items if result_untagged else []
    untagged_blocks = result_untagged.untagged_blocks if result_untagged else []

    result: dict[str, Any] = {
        "jar": jar_path.name,
        "modid": modid,
        "no_lang_file": no_lang_file,
        "lang_files": lang_file_names,
        "has_en_us": bool(en_us_files),
        "has_zh_cn": bool(zh_cn_files),
        "model_items_count": len(model_items),
        "model_blocks_count": len(model_blocks),
        "known_lang_keys": len(result_untagged.known_keys) if result_untagged else 0,
        "untagged_items": untagged_items,
        "untagged_items_count": len(untagged_items),
        "untagged_blocks": untagged_blocks,
        "untagged_blocks_count": len(untagged_blocks),
        "suggested_keys": (
            _suggest_keys(untagged_items, untagged_blocks, modid)
            if no_lang_file and (untagged_items or untagged_blocks)
            else {}
        ),
    }

    return result


def _infer_modid_from_zip(zf: ZipFile) -> str:
    """从已打开的 ZIP 文件推断 modid。

    优先从 assets/<modid>/ 路径提取。这是用于回退（无语言文件）的情况，
    因为 JarParser 在这些情况下会抛出异常。
    """
    for name in zf.namelist():
        parts = name.split("/")
        if len(parts) >= 2 and parts[0] == "assets":
            candidate = parts[1]
            if candidate and candidate != "lang":
                return candidate
    return "unknown"


def _suggest_keys(
    items: list[str],
    blocks: list[str],
    modid: str,
) -> dict[str, list[str]]:
    """为没有语言文件的 mod 建议生成的语言键（冒号格式）。"""
    return {
        "items": [f"item.{modid}:{item}.name" for item in items],
        "blocks": [f"tile.{modid}:{block}.name" for block in blocks],
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

_ITEM_NAME_CLEANUP = re.compile(r"[^a-zA-Z0-9_]+")


def _name_to_english(name: str) -> str:
    """将物品的内部名称转为可读英文。

    ``redstone_sword`` -> "Redstone Sword"
    """
    from ..analyzer.model_scanner import _name_to_english
    return _name_to_english(name)


def _is_en_us(path: str) -> bool:
    """判断一个语言文件名是否为 en_us。"""
    filename = Path(path).name.lower()
    return "en_us" in filename or "en-us" in filename


def _is_zh_cn(path: str) -> bool:
    """判断一个语言文件名是否为 zh_cn。"""
    filename = Path(path).name.lower()
    return "zh_cn" in filename or "zh-cn" in filename


def _has_any_en_us(names: list[str]) -> bool:
    """检查是否存在任何 en_us 语言文件。"""
    return any(_is_en_us(n) for n in names)


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
                        print(f"      {k} -> \"{_name_to_english(stem)}\"")
                sug_blocks = suggested.get("blocks", [])[:3]
                if sug_blocks:
                    print(f"    建议生成键 (方块):")
                    for k in sug_blocks:
                        stem = k.rsplit(".", 2)[0].split(".")[-1] if "." in k else k
                        print(f"      {k} -> \"{_name_to_english(stem)}\"")

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
