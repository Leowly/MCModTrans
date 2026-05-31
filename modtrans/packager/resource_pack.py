"""Resource pack output writer.

Creates a standard Minecraft resource pack directory structure with
translated language files, ready to drop into resourcepacks/ folder.

Output structure::

    output/
    ├── pack.mcmeta
    └── assets/
        └── <modid>/
            └── lang/
                ├── zh_cn.lang   (for LEGACY: 1.12.2-)
                └── zh_cn.json   (for MODERN: 1.13+)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import (
    GameVersion,
    ModAssets,
    MODERN_PACK_FORMAT_THRESHOLD,
    LEGACY_PACK_FORMAT_MAX,
    PACK_FORMAT_MAP,
)
from ..parser.lang_parser import format_lang
from ..parser.json_parser import format_json

logger = logging.getLogger(__name__)

# Default pack.mcmeta description
_DEFAULT_DESCRIPTION = (
    "Machine-translated Simplified Chinese localization for modded Minecraft"
)


class ResourcePack:
    """Writes translated mod assets as a Minecraft resource pack.

    Usage::

        pack = ResourcePack("My Chinese Pack", pack_format=3)
        pack.write(translated_mods, Path("./output"))
    """

    def __init__(
        self,
        name: str = "Auto Translated Chinese",
        description: str = "",
        pack_format: int | None = None,
    ) -> None:
        self.name = name
        self.description = description or _DEFAULT_DESCRIPTION
        self.pack_format = pack_format  # None = auto-detect

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        translated_mods: list[ModAssets],
        output_dir: Path,
    ) -> Path:
        """Write the complete resource pack to output_dir.

        Only writes mods that have non-empty chinese_entries.

        Args:
            translated_mods: ModAssets with chinese_entries populated.
            output_dir: Where to create the resource pack directory.

        Returns:
            The output_dir path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Determine overall pack format
        pack_format = self._determine_pack_format(translated_mods)

        # Write pack.mcmeta
        self._write_mcmeta(output_dir, pack_format)

        # Write language files
        written_count = 0
        skipped_count = 0

        for mod in translated_mods:
            if not mod.chinese_entries:
                skipped_count += 1
                continue

            lang_dir = output_dir / "assets" / mod.modid / "lang"
            lang_dir.mkdir(parents=True, exist_ok=True)

            if mod.game_version == GameVersion.LEGACY:
                file_path = lang_dir / "zh_cn.lang"
                content = format_lang(mod.chinese_entries)
                file_path.write_text(content, encoding="utf-8")
            elif mod.game_version == GameVersion.MODERN:
                file_path = lang_dir / "zh_cn.json"
                content = format_json(mod.chinese_entries)
                file_path.write_text(content, encoding="utf-8")
            else:
                # UNKNOWN — write both formats for safety
                lang_path = lang_dir / "zh_cn.lang"
                lang_path.write_text(
                    format_lang(mod.chinese_entries), encoding="utf-8"
                )
                json_path = lang_dir / "zh_cn.json"
                json_path.write_text(
                    format_json(mod.chinese_entries), encoding="utf-8"
                )

            written_count += 1

        # Also write a merged zh_cn file in the root assets for modpacks
        # that need all translations in one place
        self._write_merged(output_dir, translated_mods, pack_format)

        logger.info(
            "Resource pack written: %d mods, %d skipped (no translations)",
            written_count,
            skipped_count,
        )
        return output_dir

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _determine_pack_format(self, mods: list[ModAssets]) -> int:
        """Determine the best pack_format for the resource pack.

        If the user explicitly set a pack_format, use that.
        Otherwise, default to the majority game version:
        - Majority LEGACY → pack_format 3 (1.12.2)
        - Majority MODERN → pack_format 4 (1.13 minimum)
        """
        if self.pack_format is not None:
            return self.pack_format

        legacy_count = sum(
            1 for m in mods if m.game_version == GameVersion.LEGACY
        )
        modern_count = sum(
            1 for m in mods if m.game_version == GameVersion.MODERN
        )

        if legacy_count > modern_count:
            return LEGACY_PACK_FORMAT_MAX  # 3
        return MODERN_PACK_FORMAT_THRESHOLD  # 4

    def _write_mcmeta(self, output_dir: Path, pack_format: int) -> None:
        """Write pack.mcmeta to the output directory."""
        mcmeta = {
            "pack": {
                "pack_format": pack_format,
                "description": self.description,
            }
        }

        # Add supported formats info as a comment-like field
        # (Minecraft ignores unknown fields)
        version_range = PACK_FORMAT_MAP.get(pack_format, "unknown")
        mcmeta["pack"]["_mc_version_range"] = version_range

        mcmeta_path = output_dir / "pack.mcmeta"
        mcmeta_path.write_text(
            json.dumps(mcmeta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_merged(
        self,
        output_dir: Path,
        mods: list[ModAssets],
        pack_format: int,
    ) -> None:
        """Write a merged zh_cn.json file for convenience."""
        merged: dict[str, str] = {}

        for mod in mods:
            if mod.chinese_entries:
                merged.update(mod.chinese_entries)

        if not merged:
            return

        merged_dir = output_dir / "assets" / "minecraft" / "lang"
        merged_dir.mkdir(parents=True, exist_ok=True)

        # Always write a merged JSON for reference
        merged_path = merged_dir / "zh_cn_merged.json"
        merged_path.write_text(
            format_json(merged), encoding="utf-8"
        )

    def _write_lang(self, path: Path, entries: dict[str, str]) -> None:
        """Write .lang format file."""
        path.write_text(format_lang(entries), encoding="utf-8")

    def _write_json(self, path: Path, entries: dict[str, str]) -> None:
        """Write .json format file."""
        path.write_text(format_json(entries), encoding="utf-8")
