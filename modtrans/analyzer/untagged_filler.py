"""Untagged item filler — auto-generate missing entries from model files.

Some mods have items/blocks with 3D model files but no corresponding
language file entries. This causes the game to display raw registry IDs
like "item.modid.item_name.name" instead of readable names.

This module now delegates model scanning to ``model_scanner``, keeping only
the application logic (inserting detected gaps into ModAssets).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import ModAssets

logger = logging.getLogger(__name__)


def find_untagged_gaps(mods: list[ModAssets]) -> list[dict]:
    """Find items and blocks with model files but no lang entries.

    Delegates to ``model_scanner.generate_gaps()``.

    Args:
        mods: Parsed mod assets (must have jar_path and english_entries).

    Returns:
        List of gap dicts: {source_modid, key, suggested_en, item_name, item_type}
    """
    from .model_scanner import generate_gaps as _generate_gaps
    return _generate_gaps(mods)


def apply_untagged_gaps(mods: list[ModAssets], gaps: list[dict]) -> int:
    """Insert detected gaps into mod English entries.

    Args:
        mods: Mod assets to modify.
        gaps: Gap dicts from find_untagged_gaps().

    Returns:
        Number of entries added.
    """
    mod_map: dict[str, ModAssets] = {m.modid: m for m in mods}
    added = 0

    for g in gaps:
        src = mod_map.get(g["source_modid"])
        if not src:
            continue

        key = g["key"]
        suggested = g.get("suggested_en", "")

        if key not in src.english_entries and suggested:
            src.english_entries[key] = suggested
            added += 1
            if added <= 20:
                logger.info(
                    "补充模型条目: [%s] %s = %s",
                    src.modid, key, suggested,
                )

    if added > 20:
        logger.info("共补充 %d 个模型条目 (前20个已显示)", added)

    return added


def find_and_apply(mods: list[ModAssets]) -> int:
    """Convenience: find + apply in one call."""
    return apply_untagged_gaps(mods, find_untagged_gaps(mods))
