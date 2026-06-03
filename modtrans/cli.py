"""Command-line interface for Minecraft Mod Translation Tool."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import click

from . import __version__
from .config import AppConfig, generate_example_config, load_config
from .utils.logging_setup import setup_logging

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = Path("./output")


# ======================================================================
# Folder picker
# ======================================================================

def _pick_mods_folder() -> tuple[Path, str]:
    """打开文件夹选择器，智能识别整合包根目录 vs mods 文件夹。

    返回 (mods目录路径, MC版本号)。
    """
    path = _try_native_dialog()
    if path is not None:
        click.echo(f"已选择: {path}")
        return _resolve_mods_path(path)

    click.echo("\n未选择文件夹。请输入整合包或 mods 文件夹路径:")
    while True:
        raw = click.prompt("路径", default="").strip()
        if not raw:
            click.echo("路径不能为空。", err=True)
            continue
        for q in ('"', "'", """, """, "'", "'"):
            raw = raw.strip(q)
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return _resolve_mods_path(p)
        click.echo(f"不是有效目录: {p}", err=True)


def _resolve_mods_path(path: Path) -> tuple[Path, str]:
    """解析用户选择的路径，返回 (mods目录, MC版本)。

    如果路径包含 mods/ 子目录，识别为整合包根目录，
    同时从版本 JSON 文件提取 Minecraft 版本号。
    """
    # Modpack root: has a mods/ subdirectory
    mods_subdir = path / "mods"
    if mods_subdir.is_dir():
        jar_count = len(list(mods_subdir.glob("*.jar")))
        if jar_count > 0:
            mc_ver = _detect_mc_version(path)
            if mc_ver:
                click.echo(f"检测到整合包 (MC {mc_ver}) — 自动使用 {mods_subdir} ({jar_count} 个JAR)")
            else:
                click.echo(f"检测到整合包 — 自动使用 {mods_subdir} ({jar_count} 个JAR)")
            return mods_subdir, mc_ver

    # Direct mods folder: contains .jar files
    jars = list(path.glob("*.jar"))
    if jars:
        mc_ver = _detect_mc_version(path)
        click.echo(f"检测到 mods 文件夹 — 找到 {len(jars)} 个JAR")
        return path, mc_ver

    # Neither — warn but proceed (user might point to a version folder
    # with nested mods, or the folder could be empty)
    click.echo(f"警告: {path} 中未找到 mods/ 子目录或 .jar 文件", err=True)
    return path, ""

def _detect_mc_version(pack_root: Path) -> str:
    """从整合包根目录的版本 JSON 文件提取 Minecraft 版本号。

    查找 <pack_name>.json，读取 clientVersion / inheritsFrom 字段。
    例如 DaH1.1/DaH1.1.json → "1.12.2"
    """
    import json as _json
    # 按目录名找对应的 JSON 文件
    json_name = pack_root.name + ".json"
    json_path = pack_root / json_name
    if not json_path.is_file():
        # 备选：找目录下任意 .json
        candidates = sorted(pack_root.glob("*.json"))
        for c in candidates:
            if c.stem not in ("options", "pack"):
                json_path = c
                break
    try:
        data = _json.loads(json_path.read_text(encoding="utf-8"))
        # 优先级: clientVersion > inheritsFrom > assetIndex.id
        ver = data.get("clientVersion", "")
        if not ver:
            ver = data.get("inheritsFrom", "")
        if not ver:
            ver = data.get("assetIndex", {}).get("id", "")
        return ver
    except Exception:
        return ""


def _try_native_dialog() -> Optional[Path]:
    """Try to open a native OS folder picker. Returns None if unavailable or cancelled."""
    try:
        import tkinter as tk
        import tkinter.filedialog as fd
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result = fd.askdirectory(title="请选择 Minecraft mods 文件夹")
        root.destroy()
        if result:
            return Path(result)
        return None
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import subprocess
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 'Add-Type -AssemblyName System.Windows.Forms;'
                 '$d=New-Object System.Windows.Forms.FolderBrowserDialog;'
                 '$d.Description="请选择 Minecraft mods 文件夹";'
                 'if($d.ShowDialog() -eq[System.Windows.Forms.DialogResult]::OK){$d.SelectedPath}'],
                capture_output=True, text=True, timeout=60,
            )
            path_str = result.stdout.strip()
            if path_str:
                return Path(path_str)
        except Exception:
            pass

    return None


# ======================================================================
# CLI entry point
# ======================================================================

@click.group()
@click.option("--config", "-c", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="配置文件路径（TOML 格式）")
@click.option("--verbose", "-v", is_flag=True, help="启用详细 (DEBUG) 日志")
@click.version_option(version=__version__, prog_name="modtrans")
@click.pass_context
def main(ctx: click.Context, config: Optional[Path], verbose: bool) -> None:
    """Minecraft Mod 汉化工具 (ModTrans).

    自动提取 Minecraft 整合包 mod JAR 中的语言文件，
    调用 AI 批量翻译为简体中文，输出标准资源包。
    支持 1.12.2- (.lang) 和 1.13+ (.json) 语言文件格式。
    """
    ctx.ensure_object(dict)
    try:
        app_config = load_config(config)
    except FileNotFoundError as e:
        click.echo(f"错误: {e}", err=True)
        ctx.exit(1)
    ctx.obj["config"] = app_config
    log_level = "DEBUG" if verbose else app_config.general.log_level
    setup_logging(log_level)
    ctx.obj["verbose"] = verbose


# ======================================================================
# translate — full pipeline
# ======================================================================

@main.command()
@click.option("--mods-dir", "-m", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="mods 文件夹路径（跳过文件夹选择器）")
@click.option("--output", "-o", type=click.Path(path_type=Path),
              help=f"输出目录（默认: {_DEFAULT_OUTPUT_DIR}）")
@click.option("--dry-run", is_flag=True, help="仅解析 JAR，不调用 AI 翻译")
@click.option("--api-key", help="API 密钥（覆盖配置文件中的设置）")
@click.option("--generate-untagged", is_flag=True,
              help="为无语言文件的 mod 从模型文件名自动生成英文名并翻译")
@click.option("--verbose", "-v", is_flag=True, help="显示 AI 返回的原始内容 (DEBUG 日志)")
@click.pass_context
def translate(
    ctx: click.Context,
    mods_dir: Optional[Path],
    output: Optional[Path],
    dry_run: bool,
    api_key: Optional[str],
    generate_untagged: bool = False,
    verbose: bool = False,
) -> None:
    """运行完整翻译流水线: 选择文件夹 → 解析 → 翻译 → 打包输出资源包。"""
    # 启用调试日志
    if verbose:
        setup_logging("DEBUG")

    cfg: AppConfig = ctx.obj["config"]

    if mods_dir:
        mods_path, mc_version = _resolve_mods_path(mods_dir)
    else:
        click.echo(f"ModTrans v{__version__}\n")
        mods_path, mc_version = _pick_mods_folder()

    output_path = output or _DEFAULT_OUTPUT_DIR

    actual_api_key = api_key or cfg.ai.api_key
    if not dry_run and not actual_api_key:
        click.echo(
            "错误: 未配置 API 密钥。请在 modtrans.toml 中设置 api_key 或使用 --api-key 参数。",
            err=True,
        )
        sys.exit(1)
    cfg.ai.api_key = actual_api_key

    if mc_version:
        click.echo(f"MC 版本: {mc_version}")
    click.echo(f"Mods 目录: {mods_path}")
    click.echo(f"输出目录: {output_path}\n")

    # Lazy import — only loaded when command runs
    from .pipeline import parse_only, run_translation

    if dry_run:
        click.echo("=== 第1步: 解析 JAR 文件 ===")
        parse_only(cfg, mods_path, mc_version, generate_untagged, log_fn=click.echo)
        click.echo("\n试运行模式 — 仅解析，不调用 AI。")
        return

    run_translation(
        cfg=cfg,
        mods_dir=mods_path,
        output_path=output_path,
        mc_version=mc_version,
        generate_untagged=generate_untagged,
        log_fn=click.echo,
        confirm_fn=click.confirm,
    )


# ======================================================================
# parse — parse JARs only, output JSON
# ======================================================================

@main.command()
@click.option("--mods-dir", "-m", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="mods 文件夹路径")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=Path("parsed_output.json"),
              help="输出 JSON 文件路径")
@click.pass_context
def parse(ctx: click.Context, mods_dir: Optional[Path], output: Path) -> None:
    """解析 JAR 文件，将所有语言数据导出为 JSON（调试用）。"""
    import json

    from .parser.jar_parser import JarParseError, JarParser

    target, _ = _resolve_mods_path(mods_dir) if mods_dir else _pick_mods_folder()
    jar_paths = sorted(target.glob("*.jar"))
    click.echo(f"找到 {len(jar_paths)} 个 JAR 文件")

    parser = JarParser()
    results: list[dict] = []
    for jar_path in jar_paths:
        try:
            assets = parser.parse_jar(jar_path)
            results.append({
                "jar": jar_path.name, "modid": assets.modid,
                "game_version": assets.game_version.value,
                "metadata": {
                    "name": assets.metadata.name, "author": assets.metadata.author,
                    "version": assets.metadata.version,
                    "game_version": assets.metadata.game_version,
                },
                "english_entries": assets.english_entries,
                "existing_chinese": assets.existing_chinese,
                "encoding": assets.source_encoding,
            })
        except JarParseError as e:
            click.echo(f"  跳过 {jar_path.name}: {e}", err=True)
            results.append({"jar": jar_path.name, "error": str(e)})

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"\n已将 {len(results)} 个 mod 的解析结果写入 {output}")


# ======================================================================
# analyze — mod structure summary
# ======================================================================

@main.command()
@click.option("--mods-dir", "-m", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="mods 文件夹路径")
@click.pass_context
def analyze(ctx: click.Context, mods_dir: Optional[Path]) -> None:
    """分析 mod 结构，显示翻译覆盖率统计、i18n 匹配、未命名物品。"""
    from .debug_tools.analyzer import analyze_mods_extended, print_analysis
    if mods_dir:
        target, mc_version = _resolve_mods_path(mods_dir)
    else:
        target, mc_version = _pick_mods_folder()
    report = analyze_mods_extended(target, mc_version)
    print_analysis(report, mc_version)


# ======================================================================
# inspect — deep-dive a single JAR
# ======================================================================

@main.command()
@click.argument("jar_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def inspect(ctx: click.Context, jar_path: Path) -> None:
    """深入查看单个 mod JAR 文件的详细信息。"""
    from .debug_tools.inspector import inspect_jar, print_inspection
    print_inspection(inspect_jar(jar_path))


# ======================================================================
# find-untagged — find items without English names
# ======================================================================

@main.command("find-untagged")
@click.option("--mods-dir", "-m", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="mods 文件夹路径")
@click.pass_context
def find_untagged_cmd(ctx: click.Context, mods_dir: Optional[Path]) -> None:
    """查找 mod JAR 中缺少英文显示名称的物品/方块。"""
    from .debug_tools.finder import find_untagged, print_findings
    target, _ = _resolve_mods_path(mods_dir) if mods_dir else _pick_mods_folder()
    print_findings(find_untagged(target))


# ======================================================================
# cache — manage the parse cache
# ======================================================================

@main.command()
@click.option("--clear", is_flag=True, help="清除所有缓存的解析结果")
@click.option("--stats", is_flag=True, help="显示缓存统计信息")
@click.pass_context
def cache(ctx: click.Context, clear: bool, stats: bool) -> None:
    """管理 JAR 解析缓存。"""
    from .cache.disk_cache import DiskCache
    cfg: AppConfig = ctx.obj["config"]
    with DiskCache(cfg.general.cache_dir) as dc:
        if clear:
            click.echo(f"已清除 {dc.clear()} 条缓存")
        elif stats:
            info = dc.stats()
            click.echo(f"缓存条目:  {info['entries']}")
            click.echo(f"总大小:    {info['total_size_bytes'] / 1024 / 1024:.1f} MB")
            click.echo(f"数据库:    {info['db_path']}")
        else:
            click.echo("未指定操作。请使用 --clear 或 --stats。")

# ======================================================================
# i18n — 查看/导入 i18n 自动汉化数据
# ======================================================================

@main.command("i18n")
@click.option("--version", "-v", default="", help="MC 版本号（如 1.12.2），留空则显示摘要")
@click.pass_context
def i18n_cmd(ctx: click.Context, version: str) -> None:
    """查看 i18n 自动汉化模组的翻译数据。"""
    from .translator.i18n_reader import I18nReader, print_i18n_summary

    reader = I18nReader()

    if not version:
        print_i18n_summary(reader)
        return

    mods = reader.read_version(version)
    total_keys = sum(len(entries) for entries in mods.values())
    click.echo(f"i18n {version}: {len(mods)} 个 mod, {total_keys} 条汉化")
    # 显示前 10 个有最多条目的 mod
    top = sorted(mods.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    click.echo()
    click.echo("条目最多的 Mod (前10):")
    for modid, entries in top:
        click.echo(f"  {modid}: {len(entries)} 条")
    click.echo()
    click.echo("提示: i18n 汉化数据会在翻译时自动匹配使用。")


# ======================================================================
# init-config — generate example config
# ======================================================================

@main.command("init-config")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=Path("modtrans.toml"),
              help="输出配置文件路径")
@click.pass_context
def init_config(ctx: click.Context, output: Path) -> None:
    """生成示例配置文件。"""
    if output.exists():
        if not click.confirm(f"{output} 已存在，是否覆盖？"):
            return
    output.write_text(generate_example_config(), encoding="utf-8")
    click.echo(f"示例配置已写入 {output}")


# ======================================================================
# Helpers
# ======================================================================
#
# _package_output, _NoOpCache, and the entire translation pipeline have been
# extracted to modtrans/pipeline.py.  cli.py now delegates to:
#   - parse_only()   for dry-run mode
#   - run_translation() for the full pipeline
# ======================================================================

