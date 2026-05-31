"""Untagged item filler — auto-generate missing entries from model files.

Some mods have items/blocks with 3D model files but no corresponding
language file entries. This causes the game to display raw registry IDs
like "item.modid.item_name.name" instead of readable names.

This module detects those gaps by:
1. Scanning each mod JAR's models/item/ and blockstates/ directories
2. Cross-referencing against existing English language entries
3. Generating standard Forge lang keys and readable English names

Integrated into the translation pipeline so these entries get translated.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZipFile

from ..parser.encoding import decode_lang
from ..parser.lang_parser import parse_lang
from ..parser.json_parser import parse_json

if TYPE_CHECKING:
    from ..models import ModAssets

logger = logging.getLogger(__name__)

_ITEM_NAME_CLEANUP = re.compile(r"[^a-zA-Z0-9_]+")


def find_untagged_gaps(mods: list[ModAssets]) -> list[dict]:
    """Find items and blocks with model files but no lang entries.

    Re-opens each mod's JAR to scan for model files, then checks
    whether each item/block has a matching lang entry.

    Args:
        mods: Parsed mod assets (must have jar_path and english_entries).

    Returns:
        List of gap dicts: {source_modid, key, suggested_en, item_name, item_type}
    """
    gaps: list[dict] = []
    skipped_no_jar = 0

    for mod in mods:
        if not mod.jar_path or not mod.jar_path.is_file():
            skipped_no_jar += 1
            continue

        try:
            mod_gaps = _scan_jar(mod)
            gaps.extend(mod_gaps)
        except Exception as e:
            logger.warning(
                "无法扫描 %s 的模型文件: %s",
                mod.jar_path.name, e,
            )

    if skipped_no_jar:
        logger.debug("%d 个 mod 无 JAR 路径，已跳过", skipped_no_jar)

    return gaps


def _scan_jar(mod: ModAssets) -> list[dict]:
    """Scan a single JAR for untagged items/blocks."""
    with ZipFile(mod.jar_path, "r") as zf:
        names = zf.namelist()

        # Collect model file names
        model_items: set[str] = set()
        model_blocks: set[str] = set()
        for name in names:
            if "models/item/" in name and name.endswith(".json"):
                model_items.add(Path(name).stem)
            elif "blockstates/" in name and name.endswith(".json"):
                model_blocks.add(Path(name).stem)

        if not model_items and not model_blocks:
            return []

        # Collect known lang keys
        known_keys: set[str] = set(mod.english_entries.keys())

        # Also check if the mod uses .lang or .json keys WITHOUT .name suffix
        # Some mods (like SpartanWeaponry) use keys like "item.mod:id.subtype" without .name
        has_no_dot_name_keys = any(
            k for k in known_keys
            if k.startswith(f"item.{mod.modid}:") and not k.endswith(".name")
        )
        # Check all known keys to build a set of model-name-derived matches
        model_roots: set[str] = set()
        for k in known_keys:
            # Extract potential root from keys like "item.modid:item_name.name"
            parts = k.rsplit(".", 1)
            if len(parts) == 2:
                root = parts[0]
                model_roots.add(root)

        # Find untagged items
        mod_gaps: list[dict] = []
        for item_name in sorted(model_items):
            if _item_has_key(item_name, mod.modid, known_keys, model_roots, has_no_dot_name_keys):
                continue
            key = f"item.{mod.modid}:{item_name}.name"
            if has_no_dot_name_keys:
                # Some mods use format without .name
                alt_key = f"item.{mod.modid}:{item_name}"
                if alt_key in known_keys:
                    continue
            suggested = _name_to_english(item_name)
            mod_gaps.append({
                "source_modid": mod.modid,
                "key": key,
                "suggested_en": suggested,
                "item_name": item_name,
                "item_type": "item",
            })

        for block_name in sorted(model_blocks):
            if _item_has_key(block_name, mod.modid, known_keys, model_roots, has_no_dot_name_keys):
                continue
            key = f"tile.{mod.modid}:{block_name}.name"
            suggested = _name_to_english(block_name)
            mod_gaps.append({
                "source_modid": mod.modid,
                "key": key,
                "suggested_en": suggested,
                "item_name": block_name,
                "item_type": "block",
            })

        if mod_gaps:
            logger.info(
                "%s: %d 个未命名物品/方块",
                mod.jar_path.name, len(mod_gaps),
            )

        return mod_gaps


def _item_has_key(
    name: str,
    modid: str,
    known_keys: set[str],
    model_roots: set[str],
    has_no_dot_name_keys: bool,
) -> bool:
    """Check if an item/block name already has a lang key.

    Tries multiple candidate patterns.
    """
    # Standard candidates
    candidates = [
        f"item.{modid}:{name}.name",
        f"tile.{modid}:{name}.name",
        f"block.{modid}:{name}.name",
        f"item.{name}.name",
        f"tile.{name}.name",
    ]

    # Mods using colon-based keys like "item.modid:item_name"
    if has_no_dot_name_keys:
        candidates.extend([
            f"item.{modid}:{name}",
            f"tile.{modid}:{name}",
            f"block.{modid}:{name}",
        ])

    for c in candidates:
        if c in known_keys:
            return True

    # Check model roots
    for root in model_roots:
        if root.endswith(f":{name}") or root.endswith(f".{name}"):
            return True

    return False


def _name_to_english(name: str) -> str:
    """Convert an internal registry name to readable English.

    redstone_sword -> "Redstone Sword"
    copper_furnace -> "Copper Furnace"
    """
    cleaned = _ITEM_NAME_CLEANUP.sub(" ", name)
    return " ".join(w.capitalize() for w in cleaned.split())


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
