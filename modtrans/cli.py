"""Command-line interface for Minecraft Mod Translation Tool."""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
import time
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .config import AppConfig, load_config, generate_example_config
from .models import ModAssets, PipelineReport, TranslationResult
from .parser.jar_parser import JarParser, JarParseError
from .cache.disk_cache import DiskCache
from .translator.batcher import Batcher, BatchingStrategy
from .translator.ai_client import AIClient
from .packager.resource_pack import ResourcePack
from .debug_tools.analyzer import analyze_mods, print_analysis
from .debug_tools.inspector import inspect_jar, print_inspection
from .debug_tools.finder import find_untagged, print_findings
from .utils.logging_setup import setup_logging
from .utils.progress import create_progress

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = Path("./modtrans_output")


# ======================================================================
# Folder picker
# ======================================================================

def _pick_folder() -> Path:
    """Open a system folder picker dialog. Falls back to text input on cancel.

    Returns:
        User-selected directory path.
    """
    path = _try_native_dialog()
    if path is not None:
        click.echo(f"Selected: {path}")
        return path

    # Fallback: manual input
    click.echo("\nNo folder selected. Please enter the mods directory path:")
    while True:
        raw = click.prompt("Mods directory", default="").strip()
        if not raw:
            click.echo("Path cannot be empty.", err=True)
            continue
        # Strip surrounding quotes (single, double, or fancy quotes)
        for q in ('"', "'", "“", "”", "‘", "’"):
            raw = raw.strip(q)
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return p
        click.echo(f"Not a valid directory: {p}", err=True)


def _try_native_dialog() -> Optional[Path]:
    """Try to open a native OS folder picker. Returns None if unavailable or cancelled."""
    # Try tkinter first (bundled with Python on Windows/macOS)
    try:
        import tkinter.filedialog as fd
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()  # Hide the root window
        root.attributes("-topmost", True)
        result = fd.askdirectory(title="Select your Minecraft mods folder")
        root.destroy()
        if result:
            return Path(result)
        return None
    except Exception:
        pass

    # Fallback: try Windows PowerShell dialog
    if sys.platform == "win32":
        try:
            import subprocess
            script = '''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Select your Minecraft mods folder"
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    $dialog.SelectedPath
}
'''
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", script],
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
@click.option(
    "--config", "-c",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to config file (TOML format)",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose (DEBUG) logging",
)
@click.version_option(version=__version__, prog_name="modtrans")
@click.pass_context
def main(ctx: click.Context, config: Optional[Path], verbose: bool) -> None:
    """Minecraft Mod Chinese Translation Tool.

    Automated Chinese localization for Minecraft mod JAR files.
    Supports 1.12.2 (.lang) and 1.13+ (.json) language formats.
    """
    ctx.ensure_object(dict)
    try:
        app_config = load_config(config)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)
    ctx.obj["config"] = app_config
    log_level = "DEBUG" if verbose else app_config.general.log_level
    setup_logging(log_level)
    ctx.obj["verbose"] = verbose


# ======================================================================
# translate — full pipeline
# ======================================================================

@main.command()
@click.option(
    "--mods-dir", "-m",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to mods directory (skips folder picker if provided)",
)
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    help=f"Output directory (default: {_DEFAULT_OUTPUT_DIR})",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Parse JARs only, skip AI translation",
)
@click.option(
    "--api-key",
    help="API key (overrides config file)",
)
@click.pass_context
def translate(
    ctx: click.Context,
    mods_dir: Optional[Path],
    output: Optional[Path],
    dry_run: bool,
    api_key: Optional[str],
) -> None:
    """Run the full translation pipeline: pick folder → parse → translate → package."""
    cfg: AppConfig = ctx.obj["config"]

    # --- Resolve mods directory ---
    if mods_dir:
        mods_path = mods_dir
    else:
        click.echo(f"ModTrans v{__version__}\n")
        mods_path = _pick_folder()

    output_path = output or _DEFAULT_OUTPUT_DIR

    # --- Resolve API key ---
    actual_api_key = api_key or cfg.ai.api_key
    if not dry_run and not actual_api_key:
        click.echo(
            "Error: No API key configured. Set api_key in modtrans.toml or use --api-key.",
            err=True,
        )
        sys.exit(1)
    cfg.ai.api_key = actual_api_key

    click.echo(f"Mods directory: {mods_path}")
    click.echo(f"Output directory: {output_path}")
    click.echo()

    total_start = time.monotonic()
    report = PipelineReport()

    # --- Stage 1: Parse JARs ---
    click.echo("=== Stage 1: Parsing JARs ===")
    jar_paths = sorted(mods_path.glob("*.jar"))
    if not jar_paths:
        click.echo(f"No JAR files found in {mods_path}")
        return

    report.total_jars = len(jar_paths)
    parser = JarParser()
    all_mod_assets: list[ModAssets] = []

    if cfg.cache.enabled:
        cache_ctx = DiskCache(cfg.general.cache_dir)
    else:
        cache_ctx = _NoOpCache()

    with cache_ctx as cache:
        for jar_path in create_progress(jar_paths, desc="Parsing JARs", unit="jar"):
            try:
                jar_hash = DiskCache.hash_jar(jar_path)
                cached = cache.get(jar_hash) if cfg.cache.enabled else None

                if cached:
                    all_mod_assets.append(cached)
                    report.parsed_jars += 1
                    continue

                assets = parser.parse_jar(jar_path)
                all_mod_assets.append(assets)
                report.parsed_jars += 1
                report.total_keys += len(assets.english_entries)

                if cfg.cache.enabled:
                    cache.put(jar_hash, assets)

            except JarParseError as e:
                logger.warning("Skipping %s: %s", jar_path.name, e)
                report.failed_jars += 1
                report.errors.append(f"{jar_path.name}: {e}")

    click.echo(
        f"Parsed {report.parsed_jars}/{report.total_jars} JARs "
        f"({report.failed_jars} failed), {report.total_keys} total keys"
    )

    if dry_run:
        click.echo("\nDry run — stopping after parse.")
        return

    # --- Stage 2: Build batches ---
    click.echo("\n=== Stage 2: Building translation batches ===")
    strategy = BatchingStrategy(cfg.batcher.strategy)
    batcher = Batcher(
        strategy=strategy,
        max_batch_keys=cfg.batcher.max_batch_keys,
        min_keys_for_solo=cfg.batcher.min_keys_for_solo,
    )
    batches = batcher.group(all_mod_assets)

    if not batches:
        click.echo("No entries need translation — all mods already fully translated!")
        cfg.general.output_dir = output_path
        _package_output(all_mod_assets, cfg, report, total_start)
        return

    click.echo(f"{len(batches)} batches, {sum(b.total_keys for b in batches)} keys to translate")

    # --- Stage 3: AI Translation ---
    click.echo("\n=== Stage 3: AI Translation ===")
    click.echo(f"Model: {cfg.ai.model}  |  API: {cfg.ai.api_base}")

    async def _run_translation() -> None:
        async with AIClient(cfg.ai) as ai_client:
            for batch in create_progress(batches, desc="Translating", unit="batch"):
                result = await ai_client.translate_batch(batch)
                report.api_calls += 1

                if result.success:
                    for mod in batch.mods:
                        mod_translations = {
                            k: v
                            for k, v in result.translations.items()
                            if k in mod.english_entries
                        }
                        for k, zh_val in mod.existing_chinese.items():
                            en_val = mod.english_entries.get(k)
                            if en_val is not None and zh_val.strip() != en_val.strip():
                                mod_translations[k] = zh_val
                        mod.chinese_entries = mod_translations

                    tokens = result.usage.get("total_tokens", 0)
                    report.total_tokens += tokens
                else:
                    click.echo(f"  {batch.batch_id}: FAILED — {result.error}", err=True)
                    for mod in batch.mods:
                        mod.chinese_entries = dict(mod.english_entries)

        for mod in all_mod_assets:
            report.translated_keys += len(mod.chinese_entries)
            already_good = {
                k for k, v in mod.existing_chinese.items()
                if k in mod.english_entries and v.strip() != mod.english_entries[k].strip()
            }
            report.skipped_keys += len(already_good)

    asyncio.run(_run_translation())

    # --- Stage 4: Package ---
    cfg.general.output_dir = output_path
    _package_output(all_mod_assets, cfg, report, total_start)


# ======================================================================
# parse — parse JARs only, output JSON
# ======================================================================

@main.command()
@click.option(
    "--mods-dir", "-m",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to mods directory",
)
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=Path("parsed_output.json"),
    help="Output JSON file path",
)
@click.pass_context
def parse(ctx: click.Context, mods_dir: Optional[Path], output: Path) -> None:
    """Parse JAR files and dump all language data to JSON (for debugging)."""
    target = mods_dir or _pick_folder()
    jar_paths = sorted(target.glob("*.jar"))
    click.echo(f"Found {len(jar_paths)} JAR files")

    import json
    parser = JarParser()
    results: list[dict] = []

    for jar_path in create_progress(jar_paths, desc="Parsing JARs", unit="jar"):
        try:
            assets = parser.parse_jar(jar_path)
            results.append({
                "jar": jar_path.name,
                "modid": assets.modid,
                "game_version": assets.game_version.value,
                "metadata": {
                    "name": assets.metadata.name,
                    "author": assets.metadata.author,
                    "version": assets.metadata.version,
                    "game_version": assets.metadata.game_version,
                },
                "english_entries": assets.english_entries,
                "existing_chinese": assets.existing_chinese,
                "encoding": assets.source_encoding,
            })
        except JarParseError as e:
            click.echo(f"  Skipping {jar_path.name}: {e}", err=True)
            results.append({"jar": jar_path.name, "error": str(e)})

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"\nWritten {len(results)} mod parse results to {output}")


# ======================================================================
# analyze — mod structure summary
# ======================================================================

@main.command()
@click.option(
    "--mods-dir", "-m",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to mods directory",
)
@click.pass_context
def analyze(ctx: click.Context, mods_dir: Optional[Path]) -> None:
    """Analyze mod structure and show translation coverage statistics."""
    target = mods_dir or _pick_folder()
    report = analyze_mods(target)
    print_analysis(report)


# ======================================================================
# inspect — deep-dive a single JAR
# ======================================================================

@main.command()
@click.argument(
    "jar_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.pass_context
def inspect(ctx: click.Context, jar_path: Path) -> None:
    """Inspect a single mod JAR file in detail."""
    result = inspect_jar(jar_path)
    print_inspection(result)


# ======================================================================
# find-untagged — find items without English names
# ======================================================================

@main.command("find-untagged")
@click.option(
    "--mods-dir", "-m",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to mods directory",
)
@click.pass_context
def find_untagged_cmd(ctx: click.Context, mods_dir: Optional[Path]) -> None:
    """Find items that may lack English display names in mod JARs."""
    target = mods_dir or _pick_folder()
    results = find_untagged(target)
    print_findings(results)


# ======================================================================
# cache — manage the parse cache
# ======================================================================

@main.command()
@click.option("--clear", is_flag=True, help="Clear all cached parse results")
@click.option("--stats", is_flag=True, help="Show cache statistics")
@click.pass_context
def cache(ctx: click.Context, clear: bool, stats: bool) -> None:
    """Manage the JAR parse cache."""
    cfg: AppConfig = ctx.obj["config"]
    disk_cache = DiskCache(cfg.general.cache_dir)

    with disk_cache:
        if clear:
            count = disk_cache.clear()
            click.echo(f"Cleared {count} cached entries")
        elif stats:
            info = disk_cache.stats()
            click.echo(f"Cache entries: {info['entries']}")
            click.echo(f"Total size:    {info['total_size_bytes'] / 1024 / 1024:.1f} MB")
            click.echo(f"Database:      {info['db_path']}")
        else:
            click.echo("No action specified. Use --clear or --stats.")


# ======================================================================
# init-config — generate example config
# ======================================================================

@main.command("init-config")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=Path("modtrans.toml"),
    help="Output config file path",
)
@click.pass_context
def init_config(ctx: click.Context, output: Path) -> None:
    """Generate an example configuration file."""
    if output.exists():
        if not click.confirm(f"{output} already exists. Overwrite?"):
            return
    output.write_text(generate_example_config(), encoding="utf-8")
    click.echo(f"Example config written to {output}")


# ======================================================================
# Helpers
# ======================================================================

def _package_output(
    mods: list[ModAssets],
    cfg: AppConfig,
    report: PipelineReport,
    total_start: float,
) -> None:
    """Package translated mods into a resource pack and print final report."""
    click.echo("\n=== Stage 4: Packaging ===")

    pack_format = None
    if cfg.packager.pack_format != "auto":
        try:
            pack_format = int(cfg.packager.pack_format)
        except ValueError:
            pass

    pack = ResourcePack(
        name=cfg.packager.pack_name,
        description=cfg.packager.pack_description,
        pack_format=pack_format,
    )
    output = pack.write(mods, cfg.general.output_dir)

    report.duration_seconds = time.monotonic() - total_start
    click.echo()
    click.echo("=" * 50)
    click.echo("  Translation Pipeline Complete")
    click.echo("=" * 50)
    click.echo(f"  JARs parsed:     {report.parsed_jars}/{report.total_jars}")
    click.echo(f"  Total keys:      {report.total_keys}")
    click.echo(f"  Translated:      {report.translated_keys}")
    click.echo(f"  Skipped (zh_cn): {report.skipped_keys}")
    click.echo(f"  API calls:       {report.api_calls}")
    click.echo(f"  Total tokens:    {report.total_tokens}")
    click.echo(f"  Duration:        {report.duration_seconds:.1f}s")
    click.echo(f"  Output:          {output}")
    if report.errors:
        click.echo(f"  Errors:          {len(report.errors)}")
        for err in report.errors[:5]:
            click.echo(f"    - {err}")
        if len(report.errors) > 5:
            click.echo(f"    ... and {len(report.errors) - 5} more")
    click.echo("=" * 50)


class _NoOpCache:
    """No-op cache for when caching is disabled."""

    def __enter__(self) -> "_NoOpCache":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    @staticmethod
    def get(jar_hash: str) -> None:
        return None

    @staticmethod
    def put(jar_hash: str, assets: ModAssets) -> None:
        pass
