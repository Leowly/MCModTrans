"""分析 mods 结构 — 翻译覆盖率、i18n 匹配、未命名物品检测。

Usage::

    modtrans analyze -m ./mods
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from ..models import GameVersion
from ..parser.jar_parser import JarParser, JarParseError

logger = logging.getLogger(__name__)


def analyze_mods(mods_dir: Path) -> dict[str, Any]:
    """分析 mods 目录中所有 JAR，返回汇总统计和每个 mod 的详情。

    Args:
        mods_dir: mods 文件夹路径。

    Returns:
        包含统计数字和每个 mod 详情的字典。
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

            # 统计 zh_cn 中值仍为英文的条目
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


# ---------------------------------------------------------------------------
# 新的分析器: i18n 匹配 + 未命名物品检测
# ---------------------------------------------------------------------------


def analyze_mods_extended(
    mods_dir: Path,
    mc_version: str = "",
) -> dict[str, Any]:
    """增强版分析 — 包含 i18n 匹配和未命名物品检测。

    加载 i18n 数据 → 按 modid 匹配 → 统计 i18n 命中
    扫描模型文件 → 比对语言条目 → 报告未命名物品
    """
    basic = analyze_mods(mods_dir)

    # --- i18n 匹配 ---
    i18n_matched = 0
    i18n_keys_total = 0
    if mc_version:
        try:
            from ..translator.i18n_reader import I18nReader
            reader = I18nReader()
            best = reader.find_best_version(mc_version)
            if best:
                i18n_data = reader.read_version(best)
                i18n_version = best
                for mod in basic["mods"]:
                    modid = mod.get("modid", "")
                    if modid in i18n_data:
                        i18n_entries = i18n_data[modid]
                        mod["i18n_keys"] = len(i18n_entries)
                        i18n_matched += 1
                        i18n_keys_total += len(i18n_entries)
                    else:
                        mod["i18n_keys"] = 0
        except Exception:
            i18n_version = ""

    # --- 未命名物品检测 ---
    untagged_mods: list[dict[str, Any]] = []
    for mod in basic["mods"]:
        if mod["format"] == "no lang":
            continue
        # 用 finder 的逻辑检测
        jar_name = mod["jar"]
        jar_path = mods_dir / jar_name
        if not jar_path.is_file():
            continue
        try:
            ut = _check_untagged(jar_path, mod.get("modid", ""))
            if ut:
                mod["untagged_items"] = ut["untagged_items"]
                mod["untagged_blocks"] = ut["untagged_blocks"]
        except Exception:
            pass

    basic["i18n_version"] = locals().get("i18n_version", "")
    basic["i18n_matched"] = i18n_matched
    basic["i18n_keys_total"] = i18n_keys_total

    return basic


def _check_untagged(jar_path: Path, modid: str) -> dict[str, Any] | None:
    """检查单个 JAR 中的未命名物品/方块。"""
    from ..parser.encoding import decode_lang
    from ..parser.lang_parser import parse_lang
    from ..parser.json_parser import parse_json

    with ZipFile(jar_path, "r") as zf:
        names = zf.namelist()

        # 收集模型物品/方块
        model_items: set[str] = set()
        model_blocks: set[str] = set()
        for name in names:
            if "models/item/" in name and name.endswith(".json"):
                model_items.add(Path(name).stem)
            elif "blockstates/" in name and name.endswith(".json"):
                model_blocks.add(Path(name).stem)

        if not model_items and not model_blocks:
            return None

        # 解析已有的 en_us 键
        known_keys: set[str] = set()
        for name in names:
            if "/lang/" not in name:
                continue
            fn = name.lower()
            if "en_us" not in fn and "en-us" not in fn:
                continue
            try:
                raw = zf.read(name)
                if name.endswith(".lang"):
                    text, _ = decode_lang(raw)
                    known_keys.update(parse_lang(text).keys())
                else:
                    known_keys.update(parse_json(raw).keys())
            except Exception:
                pass

        # 检查每个模型物品是否有对应 lang key
        untagged_items = sorted(
            i for i in model_items
            if not _has_lang_key(i, modid, known_keys)
        )
        untagged_blocks = sorted(
            b for b in model_blocks
            if not _has_lang_key(b, modid, known_keys)
        )

        if untagged_items or untagged_blocks:
            return {
                "untagged_items": untagged_items,
                "untagged_blocks": untagged_blocks,
            }
        return None


def _has_lang_key(name: str, modid: str, known_keys: set[str]) -> bool:
    """检查物品/方块名是否有对应的 lang key。"""
    candidates = [
        f"item.{modid}.{name}.name",
        f"tile.{modid}.{name}.name",
        f"block.{modid}.{name}.name",
        f"{modid}.{name}.name",
        f"item.{name}.name",
        f"tile.{name}.name",
    ]
    if any(c in known_keys for c in candidates):
        return True
    suffix = f".{name}.name"
    return any(k.endswith(suffix) for k in known_keys)


# ---------------------------------------------------------------------------
# Rich 表格输出
# ---------------------------------------------------------------------------


def print_analysis(
    report: dict[str, Any],
    mc_version: str = "",
) -> None:
    """用 Rich 表格打印分析报告（CJK 字符自动对齐）。"""

    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        from rich import box
        use_rich = True
    except ImportError:
        use_rich = False

    console = Console() if use_rich else None

    def _v(val: Any, dash: str = "-", suffix: str = "") -> str:
        """格式化单元格值。"""
        if isinstance(val, (int, float)):
            return f"{val}{suffix}"
        return dash

    def _green(text: str) -> str | Text:
        return Text(text, style="green") if use_rich else text

    def _yellow(text: str) -> str | Text:
        return Text(text, style="yellow") if use_rich else text

    def _dim(text: str) -> str | Text:
        return Text(text, style="dim") if use_rich else text

    print()
    print("=" * 72)
    print("  MC Mod 翻译分析报告")
    print("=" * 72)
    print(f"  JAR 总数:          {report['total_jars']}")
    print(f"  含有语言文件:      {report['parsed']}")
    print(f"  无语言文件:        {report['no_lang']}  (工具库/前置模组，无需翻译)")

    # 格式说明
    if report["modern_format"] > 0 and report["legacy_format"] > report["modern_format"]:
        # 多数是 .lang 的旧版包混入了几个 .json mod
        print(f"  旧版 (.lang):      {report['legacy_format']}")
        print(f"  新版 (.json):      {report['modern_format']}  (少数 1.12.2 mod 也使用 .json 格式)")
    elif report["modern_format"] > 0:
        print(f"  旧版 (.lang):      {report['legacy_format']}")
        print(f"  新版 (.json):      {report['modern_format']}")
    else:
        print(f"  旧版 (.lang):      {report['legacy_format']}")

    print(f"  英文条目总数:      {report['total_en_keys']}")
    print(f"  中文条目总数:      {report['total_zh_keys']}")
    print(f"  汉化覆盖率:        {report['overall_zh_coverage_pct']:.1f}%")

    # i18n 匹配
    i18n_ver = report.get("i18n_version", "")
    i18n_matched = report.get("i18n_matched", 0)
    if i18n_ver and i18n_matched > 0:
        print(f"  i18n ({i18n_ver}): 匹配 {i18n_matched} 个 mod, {report.get('i18n_keys_total', 0)} 条汉化可直接复用")

    print("-" * 72)

    # ------------- Rich Table -------------
    if use_rich:
        table = Table(
            box=box.SIMPLE,
            expand=False,
            padding=(0, 1),
            show_edge=False,
        )
        table.add_column("Mod", style="", min_width=26, max_width=38, no_wrap=True)
        table.add_column("EN", justify="right", style="", width=5)
        table.add_column("ZH", justify="right", style="", width=5)
        table.add_column("覆盖率", justify="right", style="", width=6)
        table.add_column("缺失", justify="right", style="", width=4)
        table.add_column("残留", justify="right", style="", width=4)

        for mod in report["mods"]:
            # 无语言文件的工具库 mod 不列入表格
            if mod["format"] == "no lang":
                continue

            jar = mod["jar"]
            name = jar[:36] + ".." if len(jar) > 38 else jar

            en_s = _v(mod["en_keys"])
            zh_s = _v(mod["zh_keys"])
            cov_val = mod["zh_coverage_pct"]
            if isinstance(cov_val, (int, float)):
                if cov_val == 100:
                    cov_s = _green(f"{cov_val:.1f}%")
                elif cov_val == 0 and mod["en_keys"] > 0:
                    cov_s = _yellow(f"{cov_val:.1f}%")
                else:
                    cov_s = f"{cov_val:.1f}%"
            else:
                cov_s = _dim("  -")
            miss_s = _v(mod["zh_missing"])
            eng_s = _v(mod["english_in_zh"])

            # 标记有未命名物品的 mod
            unt_items = mod.get("untagged_items", [])
            unt_blocks = mod.get("untagged_blocks", [])
            if unt_items or unt_blocks:
                name = f"🔶 {name}"

            table.add_row(name, en_s, zh_s, cov_s, miss_s, eng_s)

        # 合计行
        table.add_section()
        table.add_row(
            Text("合计", style="bold"),
            Text(str(report["total_en_keys"]), style="bold"),
            Text(str(report["total_zh_keys"]), style="bold"),
            Text(f"{report['overall_zh_coverage_pct']:.1f}%", style="bold"),
            Text(str(report["total_en_keys"] - report["total_zh_keys"]), style="bold"),
            Text(""),
        )

        console.print(table)

    else:
        # 回退到简单格式
        print(f"  {'Mod':<40} {'EN':>5} {'ZH':>5} {'覆盖%':>7} {'缺失':>5} {'E→中':>7}")
        print("-" * 72)
        for mod in report["mods"]:
            if mod["format"] == "no lang":
                continue
            jar = mod["jar"]
            name = jar[:38] + (".." if len(jar) > 40 else "")
            en_s = _v(mod["en_keys"])
            zh_s = _v(mod["zh_keys"])
            cov_val = mod["zh_coverage_pct"]
            cov_s = f"{cov_val:.1f}%" if isinstance(cov_val, (int, float)) else "  -"
            miss_s = _v(mod["zh_missing"])
            eng_s = _v(mod["english_in_zh"])
            unt_items = mod.get("untagged_items", [])
            unt_blocks = mod.get("untagged_blocks", [])
            prefix = "! " if (unt_items or unt_blocks) else "  "
            print(f"  {prefix}{name:<38} {en_s:>5} {zh_s:>5} {cov_s:>7} {miss_s:>5} {eng_s:>7}")

        print("-" * 72)
        print(f"  {'合计':<40} {report['total_en_keys']:>5} {report['total_zh_keys']:>5}")

    print("=" * 72)
    print()

    # ------------- 未翻译 -------------
    untranslated = [
        m for m in report["mods"]
        if m["zh_coverage_pct"] == 0 and m["en_keys"] > 0
    ]
    if untranslated:
        print(f"  完全没有汉化的 Mod ({len(untranslated)}):")
        for m in untranslated:
            unt_info = ""
            u_items = m.get("untagged_items", [])
            u_blocks = m.get("untagged_blocks", [])
            if u_items or u_blocks:
                unt_info = f"  ⚠ 还有 {len(u_items)} 个物品/{len(u_blocks)} 个方块在游戏里显示为原始 ID"
            print(f"    - {m['jar']}  ({m['en_keys']} 条){unt_info}")
        print()

    # ------------- 英文残留 -------------
    eng_in_zh = [m for m in report["mods"] if isinstance(m["english_in_zh"], int) and m["english_in_zh"] > 0]
    if eng_in_zh:
        print(f"  zh_cn 中残留英文的 Mod ({len(eng_in_zh)}):")
        for m in eng_in_zh:
            print(f"    - {m['jar']}  ({m['english_in_zh']} 条仍为英文)")
        print()

    # ------------- 未命名物品 -------------
    untagged_all = [
        m for m in report["mods"]
        if m.get("untagged_items") or m.get("untagged_blocks")
    ]
    if untagged_all:
        total_unt_items = sum(len(m.get("untagged_items", [])) for m in untagged_all)
        total_unt_blocks = sum(len(m.get("untagged_blocks", [])) for m in untagged_all)
        print(f"  有物品/方块缺少英文名的 Mod ({len(untagged_all)}):")
        print(f"    (共 {total_unt_items} 个物品, {total_unt_blocks} 个方块会在游戏中显示为原始 ID)")
        for m in untagged_all:
            u_items = m.get("untagged_items", [])
            u_blocks = m.get("untagged_blocks", [])
            parts = []
            if u_items:
                parts.append(f"{len(u_items)} 个物品")
            if u_blocks:
                parts.append(f"{len(u_blocks)} 个方块")
            print(f"    🔶 {m['jar']}  — {', '.join(parts)}")
        print()
        print("  提示: 运行 modtrans find-untagged 查看详情。")
        print("  翻译时加 --generate-untagged 可为这些物品自动生成中英文名。")
        print()
