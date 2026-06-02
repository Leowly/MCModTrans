# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Install dependencies (uses uv package manager, Python 3.13+)
uv sync

# Run the CLI
uv run modtrans [command] [options]

# Generate config file
uv run modtrans init-config

# Full translation pipeline (interactive folder picker)
uv run modtrans translate

# Skip folder picker, specify mods directly
uv run modtrans translate -m "D:\path\to\modpack"

# Dry run — parse JARs only, no AI calls
uv run modtrans translate --dry-run

# Enable untagged item name generation
uv run modtrans translate --generate-untagged

# Analysis and debugging
uv run modtrans analyze -m "D:\path\to\modpack"
uv run modtrans inspect "D:\path\to\mod.jar"
uv run modtrans find-untagged -m "D:\path\to\modpack"
uv run modtrans parse -o analysis.json
uv run modtrans cache --stats
uv run modtrans cache --clear
uv run modtrans i18n -v 1.12.2
```

There are no tests in this project.

## Architecture

### Configuration loading order

1. Explicit `--config / -c` CLI argument
2. `./modtrans.toml` (current directory)
3. `~/.config/modtrans/config.toml`
4. Built-in dataclass defaults (`modtrans/config.py`)

Config is TOML, loaded into an `AppConfig` dataclass with five sections: `general`, `ai`, `prompt`, `packager`, `cache`.

### Full translation pipeline (`modtrans translate`)

The pipeline runs in five stages (defined in `modtrans/cli.py:translate`):

1. **Parse JARs** — `JarParser` orchestrates ZIP extraction, version detection (legacy `.lang` vs modern `.json`), encoding detection (BOM → chardet → fallback chain), and metadata extraction. Results are cached in SQLite keyed by SHA-256 of the JAR file.

2. **Supplement & filter** — CFPA i18n community data is merged into `existing_chinese` (non-destructive — doesn't overwrite existing). Compatibility filter (`compat.py`) removes known-problematic translations (e.g., ProjectE manual text causes StackOverflowError). Cross-mod gap detection finds references between mods. Untagged item filler scans model JSON files for items/blocks missing language entries and auto-generates English names from filenames.

3. **Translation memory lookup** — `TranslationMemory` (dual SQLite + JSON storage) checks all untranslated keys before any AI call. SHA-256 hash of English text is the key.

4. **AI translation** — `Batcher` groups by author, bundles small mods, and splits oversized batches (`max_keys_per_call`). `AIClient` (async httpx) calls OpenAI-compatible API with token-bucket rate limiting + exponential backoff retry. Missed keys from all batches are collected and retranslated in a centralized phase. Translations are written back to `TranslationMemory` and synced to JSON.

5. **Package output** — `ResourcePack` writes `pack.mcmeta` + `assets/<modid>/lang/zh_cn.*` files to a ZIP.

### Key data model (`modtrans/models.py`)

- `GameVersion` enum: `LEGACY` (1.12.2-, `.lang`), `MODERN` (1.13+, `.json`), `UNKNOWN`
- `ModAssets`: per-mod container — `english_entries`, `chinese_entries` (output), `existing_chinese` (pre-existing in JAR or from i18n), `ModMetadata` (author is used as the batching key)
- `TranslationBatch`: group of mods + their entries sent in one API call. `batch_id` format: `"author_<name>"` or `"bundled_small_mods_N"`. Only contains `entries` (key→en_text) and `context_info`; no `existing_reference` field.
- `PipelineReport`: accumulates stats throughout the pipeline run

### Translation memory (`modtrans/translator/translation_memory.py`)

Dual storage: SQLite for fast lookup (SHA-256 hash primary key) + JSON (`{en_text: zh_text}`) for human editing. On startup, if JSON is newer than DB, prompts the user to sync. New AI translations are written to both stores. The `source` column tracks origin (`"ai"`, `"manual"`).

### Caching (`modtrans/cache/disk_cache.py`)

JAR parse results are cached in SQLite (`{cache_dir}/jar_cache.db`), keyed by SHA-256 of the JAR file. This means re-running on the same modpack skips all JAR parsing. Managed via `modtrans cache --stats` / `--clear`.

### Prompt strategy (`modtrans/translator/prompt.py`)

`SYSTEM_PROMPT` is a module-level frozen constant — never constructed or modified at runtime. This ensures byte-for-byte identical system messages across all API calls to maximize OpenAI prompt cache hit rate. Customization goes through `PromptConfig.custom_prefix` (prepended to the frozen prompt) or `system_prompt_file` (replaces it entirely).

`build_user_message()` constructs the per-batch user message with entries to translate and mod context. Previously it also accepted `existing_chinese` and `keep_english_keys` to show AI reference translations and ask it to decide on proper nouns — these have been removed to reduce token cost. Now it only sends the entries that need translation.

### Compatibility system (`modtrans/compat.py`)

Centralized registry of mods with known bugs triggered by Chinese text. Each entry maps `modid → (key_prefixes, reason)`. The pipeline removes matching keys from `existing_chinese` before translation, keeping them in English only. Currently covers ProjectE's `ManualFontRenderer` infinite recursion.

### Shared model scanner (`modtrans/analyzer/model_scanner.py`)

Used by three commands (`translate`, `analyze`, `find-untagged`) for consistent results. Scans `models/item/*.json`, `models/block/*.json`, and `blockstates/*.json` inside JARs, resolving parent model references. Detects items/blocks that exist in model files but have no corresponding language entry.

### Encoding detection for .lang files (`modtrans/parser/encoding.py`)

Fallback chain: BOM detection → chardet library → try UTF-8 → try Windows-1252 → try GBK → Latin-1 (never fails). This is necessary because 1.12.2 mods use arbitrary encodings with no standard.
