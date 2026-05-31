"""Core data models for MC Mod Translation Tool."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class GameVersion(Enum):
    """Minecraft game version era for language file format.

    LEGACY: 1.12.2 and below — uses .lang files (key=value per line)
    MODERN: 1.13 and above      — uses .json files ({"key": "value"})
    """

    LEGACY = "legacy"
    MODERN = "modern"
    UNKNOWN = "unknown"


@dataclass
class ModMetadata:
    """Metadata extracted from a mod JAR file."""

    modid: str
    name: str = ""
    version: str = ""
    author: str = ""  # Used as grouping key for translation batching
    description: str = ""
    game_version: str = ""  # e.g. "1.12.2", "1.18.2"
    credits: str = ""
    url: str = ""


@dataclass
class ModAssets:
    """Parsed language assets from a single mod JAR.

    Contains the source English entries and (optionally) existing Chinese
    translations extracted from the JAR.
    """

    modid: str
    game_version: GameVersion
    english_entries: dict[str, str]  # key → English display text
    chinese_entries: dict[str, str] = field(default_factory=dict)  # translated output
    existing_chinese: dict[str, str] = field(default_factory=dict)  # pre-existing zh_cn
    metadata: ModMetadata = field(default_factory=lambda: ModMetadata(""))
    jar_path: Optional[Path] = None
    source_encoding: str = "utf-8"


@dataclass
class TranslationBatch:
    """A group of mods bundled for one AI translation API call.

    Batching strategy:
    - Group by metadata.author for translation consistency
    - Bundle small mods (fewer than min_keys_for_solo entries) together
    - Split if total entries exceed max_batch_keys
    """

    batch_id: str  # e.g. "author_ObliviousSpartan" or "bundled_small_mods_1"
    mods: list[ModAssets]
    total_keys: int
    existing_reference: dict[str, str] = field(default_factory=dict)
    # ^ existing zh_cn entries from all mods in batch, shown as reference to AI
    context_info: str = ""  # Added to user prompt for translation context


@dataclass
class TranslationResult:
    """Result of a single translation API call."""

    batch: TranslationBatch
    translations: dict[str, str]  # key → Chinese text
    model: str = ""
    usage: dict = field(default_factory=dict)  # token counts from API
    success: bool = False
    error: Optional[str] = None


@dataclass
class PipelineReport:
    """Aggregated report of a full translation pipeline run."""

    total_jars: int = 0
    parsed_jars: int = 0
    failed_jars: int = 0
    total_keys: int = 0
    translated_keys: int = 0
    skipped_keys: int = 0  # Already had valid zh_cn
    api_calls: int = 0
    total_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    jar_results: list[dict] = field(default_factory=list)
    # ^ per-jar summary: {modid, keys, translated, skipped, error?}


# --- Pack format constants ---

# Map pack_format values to MC version strings
PACK_FORMAT_MAP: dict[int, str] = {
    1: "1.6.4-1.8.9",
    2: "1.9-1.10.2",
    3: "1.11-1.12.2",
    4: "1.13-1.14.4",
    5: "1.15-1.16.1",
    6: "1.16.2-1.16.5",
    7: "1.17-1.17.1",
    8: "1.18-1.18.2",
    9: "1.19-1.19.2",
    10: "1.19.3",
    11: "1.19.4",
    12: "1.20-1.20.1",
    13: "1.20.2",
    14: "1.20.3-1.20.4",
    15: "1.20.5-1.20.6",
    16: "1.21",
    17: "1.21.1",
    18: "1.21.2-1.21.3",
    19: "1.21.4",
    20: "1.21.5",
}

# pack_format >= 4 means modern (JSON) language files
MODERN_PACK_FORMAT_THRESHOLD = 4
# pack_format <= 3 means legacy (.lang) language files
LEGACY_PACK_FORMAT_MAX = 3
