
from pathlib import Path

def L(s):
    OUT.append(s)

OUT = []

# ---- parse_only ----
L('def parse_only(cfg, mods_dir, mc_version="", generate_untagged=False, log_fn=print):')
L('    from .parser.jar_parser import JarParser, JarParseError')
L('    from .cache.disk_cache import DiskCache')
L('    report = PipelineReport()')
L('    jp = sorted(mods_dir.glob("*.jar"))')
L('    if not jp:')
L('        log_fn(f"{mods_dir} 中未找到 JAR 文件")')
L('        return [], report')
L('    report.total_jars = len(jp)')
L('    parser = JarParser(generate_untagged=generate_untagged)')
L('    if generate_untagged: log_fn("已启用未命名物品生成模式")')
L('    all_mods = []')
L('    ctx = DiskCache(cfg.general.cache_dir) if cfg.cache.enabled else _NoOpCache()')
L('    with ctx as cache:')
L('        for j in jp:')
L('            try:')
L('                h = DiskCache.hash_jar(j)')
L('                c = cache.get(h) if cfg.cache.enabled else None')
L('                if c:')
L('                    all_mods.append(c); report.parsed_jars += 1')
L('                    report.total_keys += len(c.english_entries)')
L('                    continue')
L('                a = parser.parse_jar(j)')
L('                all_mods.append(a); report.parsed_jars += 1')
L('                report.total_keys += len(a.english_entries)')
L('                if cfg.cache.enabled: cache.put(h, a)')
L('            except JarParseError as e:')
L('                logger.debug("跳过 %s: %s", j.name, e)')
L('                report.failed_jars += 1')
L('    tail = f"  ({report.failed_jars} 个无语言文件)" if report.failed_jars else ""')
L('    log_fn(f"已解析 {report.parsed_jars}/{report.total_jars} 个 JAR, 共 {report.total_keys} 条文本{tail}")')
L('    return all_mods, report')
L('')

content = chr(10).join(OUT)
with open(r'D:\32685\code\MCModTrans\modtrans\pipeline.py', 'a', encoding='utf-8') as f:
    # append to existing file
    pass
Path(r'D:\32685\code\MCModTrans\modtrans\pipeline.py').write_text(
    Path(r'D:\32685\code\MCModTrans\modtrans\pipeline.py').read_text(encoding='utf-8') + chr(10) + content,
    encoding='utf-8'
)
print('appended parse_only, lines:', len(OUT))
