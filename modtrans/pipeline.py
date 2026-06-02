"""Translation pipeline module."""

from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from .config import AppConfig
from .models import PipelineReport
logger = logging.getLogger(__name__)

class _NoOpCache:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    @staticmethod
    def get(j): return None
    @staticmethod
    def put(j, a): pass

@dataclass
class TranslationOutput:
    translated_mods: list
    report: PipelineReport
    tm_hits: dict
    mc_version: str

def _resolve_mods_path(path: Path) -> tuple[Path, str]:
    md = path / "mods"
    if md.is_dir() and list(md.glob("*.jar")):
        return md, _detect_mc_version(path)
    if list(path.glob("*.jar")):
        return path, _detect_mc_version(path)
    return path, ""

def _detect_mc_version(pack_root: Path) -> str:
    import json
    jp = pack_root / (pack_root.name + ".json")
    if not jp.is_file():
        for c in sorted(pack_root.glob("*.json")):
            if c.stem not in ("options", "pack"): jp = c; break
    try:
        d = json.loads(jp.read_text(encoding="utf-8"))
        return d.get("clientVersion") or d.get("inheritsFrom") or d.get("assetIndex", {}).get("id", "")
    except Exception: return ""
def parse_only(cfg, mods_dir, mc_version="", generate_untagged=False, log_fn=print):
    from .parser.jar_parser import JarParser, JarParseError
    from .cache.disk_cache import DiskCache
    report = PipelineReport()
    jp = sorted(mods_dir.glob("*.jar"))
    if not jp:
        log_fn(f"{mods_dir} 中未找到 JAR 文件")
        return [], report
    report.total_jars = len(jp)
    parser = JarParser(generate_untagged=generate_untagged)
    if generate_untagged: log_fn("已启用未命名物品生成模式")
    all_mods = []
    ctx = DiskCache(cfg.general.cache_dir) if cfg.cache.enabled else _NoOpCache()
    with ctx as cache:
        for j in jp:
            try:
                h = DiskCache.hash_jar(j)
                c = cache.get(h) if cfg.cache.enabled else None
                if c:
                    all_mods.append(c); report.parsed_jars += 1
                    report.total_keys += len(c.english_entries)
                    continue
                a = parser.parse_jar(j)
                all_mods.append(a); report.parsed_jars += 1
                report.total_keys += len(a.english_entries)
                if cfg.cache.enabled: cache.put(h, a)
            except JarParseError as e:
                logger.debug("跳过 %s: %s", j.name, e)
                report.failed_jars += 1
    tail = f"  ({report.failed_jars} 个无语言文件)" if report.failed_jars else ""
    log_fn(f"已解析 {report.parsed_jars}/{report.total_jars} 个 JAR, 共 {report.total_keys} 条文本{tail}")
    return all_mods, report


def run_translation(
    cfg: AppConfig,
    mods_dir: Path,
    output_path: Path,
    mc_version: str = "",
    generate_untagged: bool = False,
    log_fn: Callable = print,
    confirm_fn: Callable[[str], bool] = lambda _: True,
) -> TranslationOutput:
    """Run the full translation pipeline."""
    total_start = time.monotonic()

    # --- Stage 1: Parse ---
    log_fn("=== 第1步: 解析 JAR 文件 ===")
    all_mod_assets, report = parse_only(cfg, mods_dir, mc_version, generate_untagged, log_fn)
    if not all_mod_assets:
        return TranslationOutput([], report, {}, mc_version)

    # --- i18n supplement ---
    if cfg.general.enable_i18n:
        log_fn("\n--- 加载 i18n 汉化数据 ---")
        from .translator.i18n_reader import I18nReader
        i18n_reader = I18nReader()
        best_version = None
        if mc_version:
            best_version = i18n_reader.find_best_version(mc_version)
        if not best_version:
            mc_versions = {m.metadata.game_version for m in all_mod_assets if m.metadata.game_version}
            if mc_versions:
                from collections import Counter
                best_version = i18n_reader.find_best_version(Counter(mc_versions).most_common(1)[0][0])
        if best_version:
            i18n_data = i18n_reader.read_version(best_version)
            i18n_matched = i18n_keys_added = 0
            for mod in all_mod_assets:
                if mod.modid in i18n_data:
                    for k, v in i18n_data[mod.modid].items():
                        if k not in mod.existing_chinese and k in mod.english_entries:
                            mod.existing_chinese[k] = v
                            i18n_keys_added += 1
                    i18n_matched += 1
            log_fn(f"i18n {best_version}: 匹配 {i18n_matched} 个 mod，补充 {i18n_keys_added} 条汉化参考")
        else:
            log_fn("未找到匹配的 i18n 数据")

    # --- Compat filtering ---
    from .compat import SKIP_TRANSLATION_PATTERNS
    compat_removed_total = 0
    for mod in all_mod_assets:
        entry = SKIP_TRANSLATION_PATTERNS.get(mod.modid)
        if entry is None:
            continue
        prefixes, reason = entry
        removed = 0
        for key in list(mod.existing_chinese.keys()):
            if any(key.startswith(p) for p in prefixes):
                del mod.existing_chinese[key]
                removed += 1
        if removed:
            logger.info(f"%s: 从 existing_chinese 移除 %d 个兼容性键（%s）", mod.modid, removed, reason)
            compat_removed_total += removed
    if compat_removed_total:
        log_fn(f"兼容性过滤: 移除 {compat_removed_total} 条可能触发 mod bug 的汉化")

    # --- Cross-mod gap detection ---
    if cfg.general.enable_cross_mod_fill:
        log_fn("\n--- 检测跨模组缺失翻译键 ---")
        from .analyzer.cross_mod import analyze_and_apply
        cross_mod_added = analyze_and_apply(all_mod_assets)
        if cross_mod_added:
            log_fn(f"检测并补充 {cross_mod_added} 条缺失的跨模组条目")
            report.total_keys += cross_mod_added
        else:
            log_fn("未检测到缺失的跨模组条目")

    # --- Untagged filler ---
    if cfg.general.enable_untagged_fill:
        log_fn("\n--- 检测模型文件未命名物品 ---")
        from .analyzer.untagged_filler import find_and_apply
        untagged_added = find_and_apply(all_mod_assets)
        if untagged_added:
            log_fn(f"从模型文件补充 {untagged_added} 个未命名物品/方块")
            report.total_keys += untagged_added
        else:
            log_fn("所有模型物品均有语言条目")


    # --- Translation memory ---
    from .translator.translation_memory import TranslationMemory
    tm = TranslationMemory()
    tm.open()
    tm_stats = tm.stats()
    if tm_stats["total"] > 0:
        sources = ", ".join(f"{k}={v}" for k, v in tm_stats["by_source"].items())
        log_fn(f"翻译记忆库: {tm_stats['total']} 条 ({sources})")
    else:
        log_fn("翻译记忆库: 空（首次使用，翻译后自动积累）")

    if tm.check_json_sync():
        prompt_msg = f"\n检测到 {tm.json_path.name} 有更新，是否同步到数据库？"
        if confirm_fn(prompt_msg):
            added = tm.import_json()
            log_fn(f"已从 JSON 导入 {added} 条新翻译")
        else:
            log_fn("已跳过同步")

    # --- Stage 2: Check TM ---
    log_fn("\n=== 第2步: 检查翻译记忆库 ===")
    from .translator.batcher import Batcher

    all_untranslated: dict[str, str] = {}
    for mod in all_mod_assets:
        for key in Batcher._untranslated_keys(mod):
            all_untranslated[key] = mod.english_entries[key]

    if not all_untranslated:
        log_fn("所有条目已完成翻译 — 无需调用 AI！")
        _apply_translations(all_mod_assets, {}, {})
        _compute_stats(all_mod_assets, report)
        report.duration_seconds = time.monotonic() - total_start
        _log_report(report, output_path, log_fn)
        tm.close()
        return TranslationOutput(all_mod_assets, report, {}, mc_version)

    tm_hits = tm.lookup_batch(all_untranslated)
    tm_miss_keys = {k for k in all_untranslated if k not in tm_hits}
    log_fn(f"待翻译: {len(all_untranslated)} 条")
    log_fn(f"记忆库命中: {len(tm_hits)} 条")
    log_fn(f"需要 AI 翻译: {len(tm_miss_keys)} 条")

    if not tm_miss_keys:
        log_fn("\n所有条目已在翻译记忆库中 — 跳过 AI 调用！")
        _apply_translations(all_mod_assets, tm_hits, {})
        _compute_stats(all_mod_assets, report)
        report.duration_seconds = time.monotonic() - total_start
        _log_report(report, output_path, log_fn)
        tm.close()
        return TranslationOutput(all_mod_assets, report, tm_hits, mc_version)


    # --- Stage 3: Build batches ---
    log_fn("\n=== 第3步: 构建翻译批次 ===")
    effective_max_keys = cfg.ai.max_keys_per_call
    batcher = Batcher(max_batch_keys=effective_max_keys)
    log_fn(f"每批最多 {effective_max_keys} 条")
    batches = batcher.group(all_mod_assets, key_filter=tm_miss_keys)
    log_fn(f"共 {len(batches)} 个批次, {sum(b.total_keys for b in batches)} 条待翻译")


    # --- Stage 4: AI Translation ---
    log_fn(f"\n=== 第4步: AI 翻译 ===\n模型: {cfg.ai.model}  |  API: {cfg.ai.api_base}")
    from .translator.ai_client import AIClient
    total_batches = len(batches)
    all_translations = dict(tm_hits)
    tm_new_entries: dict[str, str] = {}

    async def _run_translation():
        nonlocal all_translations, tm_new_entries
        async with AIClient(cfg.ai) as ai_client:
            all_missed: dict[str, str] = {}
            for batch_idx, batch in enumerate(batches, 1):
                log_fn(f"  [{batch_idx}/{total_batches}] {batch.batch_id}...")
                result = await ai_client.translate_batch(batch)
                report.api_calls += 1
                if result.success:
                    all_translations.update(result.translations)
                    for k, zh in result.translations.items():
                        en = batch.entries.get(k)
                        if en and zh:
                            tm_new_entries[en] = zh
                    if result.missed_entries:
                        all_missed.update(result.missed_entries)
                    report.total_tokens += result.usage.get("total_tokens", 0)
                    log_fn(f"  [{batch_idx}/{total_batches}] {batch.batch_id} OK")
                else:
                    log_fn(f"  [{batch_idx}/{total_batches}] {batch.batch_id} FAIL")
                    for k, en in batch.entries.items():
                        if k not in all_translations:
                            all_translations[k] = en

            if all_missed:
                missed_total = len(all_missed)
                missed_items = list(all_missed.items())
                retry_parts = (missed_total + effective_max_keys - 1) // effective_max_keys
                log_fn(f"\n--- 集中补译: {missed_total} 条 ---")
                for i in range(0, missed_total, effective_max_keys):
                    chunk = dict(missed_items[i : i + effective_max_keys])
                    chunk_num = i // effective_max_keys + 1
                    log_fn(f"  补译 [{chunk_num}/{retry_parts}] ({len(chunk)} 键)...")
                    try:
                        zh_result, usage2 = await ai_client.translate_missing(
                            chunk, context=f"集中补译 {chunk_num}/{retry_parts}",
                        )
                        report.total_tokens += usage2.get("total_tokens", 0)
                        all_translations.update(zh_result)
                        for k, zh in zh_result.items():
                            en = all_missed.get(k)
                            if en and zh:
                                tm_new_entries[en] = zh
                        log_fn(f"  补译 [{chunk_num}/{retry_parts}] OK")
                    except Exception as e:
                        logger.warning("集中补译 %d/%d 失败: %s", chunk_num, retry_parts, e)
                        log_fn(f"  补译 [{chunk_num}/{retry_parts}] FAIL")

            _apply_translations(all_mod_assets, all_translations, {})

    asyncio.run(_run_translation())


    _compute_stats(all_mod_assets, report)

    if tm_new_entries:
        added = tm.remember_batch(tm_new_entries, source="ai")
        tm.export_json()
        log_fn(f"翻译记忆库: 本次记忆库命中 {len(tm_hits)} 条, 新增 {added} 条")
    tm.close()

    # --- Stage 5: Package ---
    from .packager.resource_pack import ResourcePack
    log_fn("\n=== 第5步: 打包输出 ===")
    pack_format = None
    if cfg.packager.pack_format != "auto":
        try: pack_format = int(cfg.packager.pack_format)
        except ValueError: pass
    pack = ResourcePack(
        name=cfg.packager.pack_name,
        description=cfg.packager.pack_description,
        pack_format=pack_format,
    )
    output = pack.write(all_mod_assets, output_path, mc_version=mc_version)
    report.duration_seconds = time.monotonic() - total_start
    _log_report(report, output_path, log_fn)
    return TranslationOutput(all_mod_assets, report, tm_hits, mc_version)


# ======================================================================
# Internal helpers
# ======================================================================


def _apply_translations(mods, all_translations, tm_hits):
    """Write translations to mod.chinese_entries. Priority: translations > existing > english fallback."""
    for mod in mods:
        zh_out: dict[str, str] = {}
        for k in mod.english_entries:
            if k in all_translations:
                zh_out[k] = all_translations[k]
        for k, zh in mod.existing_chinese.items():
            en = mod.english_entries.get(k)
            if en is not None and zh.strip() != en.strip():
                if k not in all_translations:
                    zh_out[k] = zh
        for k, en in mod.english_entries.items():
            if k not in zh_out:
                zh_out[k] = en
        mod.chinese_entries = zh_out


def _compute_stats(mods, report):
    """Compute translated_keys and skipped_keys exactly once."""
    for mod in mods:
        report.translated_keys += len(mod.chinese_entries)
        already_good = {
            k for k, v in mod.existing_chinese.items()
            if k in mod.english_entries and v.strip() != mod.english_entries[k].strip()
        }
        report.skipped_keys += len(already_good)


def _log_report(report, output_path, log_fn):
    """Print the final pipeline report."""
    log_fn("=" * 50)
    log_fn("  翻译流水线执行完毕")
    log_fn("=" * 50)
    log_fn(f"  解析 JAR:        {report.parsed_jars}/{report.total_jars}")
    log_fn(f"  文本条数:        {report.total_keys}")
    log_fn(f"  已翻译:          {report.translated_keys}")
    log_fn(f"  跳过 (已有汉化):  {report.skipped_keys}")
    log_fn(f"  API 调用次数:    {report.api_calls}")
    log_fn(f"  Token 消耗:      {report.total_tokens}")
    log_fn(f"  总耗时:          {report.duration_seconds:.1f}s")
    if output_path:
        log_fn(f"  输出路径:        {output_path}")
    if report.errors:
        log_fn(f"  错误数:          {len(report.errors)}")
        for err in report.errors[:5]:
            log_fn(f"    - {err}")
    log_fn("=" * 50)
