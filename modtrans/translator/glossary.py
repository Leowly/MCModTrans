"""Translation glossary for Minecraft mod terminology.

Provides common Minecraft term translations that can be used as reference.
Supports loading custom glossaries from CSV or JSON files.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Built-in glossary of common Minecraft terms.
# These are embedded in the system prompt and also available for
# post-processing / validation if needed.
BUILTIN_GLOSSARY: dict[str, str] = {
    # Weapons & Tools
    "Sword": "剑",
    "Pickaxe": "镐",
    "Axe": "斧",
    "Shovel": "锹",
    "Hoe": "锄",
    "Bow": "弓",
    "Arrow": "箭",
    "Shield": "盾牌",
    # Armor
    "Helmet": "头盔",
    "Chestplate": "胸甲",
    "Leggings": "护腿",
    "Boots": "靴子",
    "Armor": "盔甲",
    # Blocks
    "Dirt": "泥土",
    "Stone": "石头",
    "Cobblestone": "圆石",
    "Gravel": "沙砾",
    "Sand": "沙子",
    "Glass": "玻璃",
    "Grass Block": "草方块",
    "Oak Wood": "橡木",
    "Birch Wood": "白桦木",
    "Spruce Wood": "云杉木",
    "Jungle Wood": "丛林木",
    "Dark Oak Wood": "深色橡木",
    "Acacia Wood": "金合欢木",
    # Items
    "Iron Ingot": "铁锭",
    "Gold Ingot": "金锭",
    "Diamond": "钻石",
    "Emerald": "绿宝石",
    "Coal": "煤炭",
    "Charcoal": "木炭",
    "Stick": "木棍",
    "String": "线",
    "Leather": "皮革",
    "Feather": "羽毛",
    "Flint": "燧石",
    "Clay": "黏土",
    "Brick": "砖",
    "Paper": "纸",
    "Book": "书",
    "Bone": "骨头",
    # Food
    "Apple": "苹果",
    "Bread": "面包",
    "Steak": "牛排",
    "Porkchop": "猪排",
    "Chicken": "鸡肉",
    "Fish": "鱼",
    "Carrot": "胡萝卜",
    "Potato": "马铃薯",
    # Mobs
    "Creeper": "苦力怕",
    "Enderman": "末影人",
    "Zombie": "僵尸",
    "Skeleton": "骷髅",
    "Spider": "蜘蛛",
    "Slime": "史莱姆",
    "Witch": "女巫",
    "Guardian": "守卫者",
    # Mechanics
    "Redstone": "红石",
    "Glowstone": "萤石",
    "Obsidian": "黑曜石",
    "Nether": "下界",
    "The End": "末地",
    "Overworld": "主世界",
    "Crafting": "合成",
    "Smelting": "烧炼",
    "Enchantment": "附魔",
    "Durability": "耐久度",
    # UI
    "Inventory": "物品栏",
    "Health": "生命值",
    "Hunger": "饥饿值",
    "Experience": "经验值",
    "Level": "等级",
    "Damage": "伤害",
    "Speed": "速度",
    # Common descriptors
    "Wooden": "木",
    "Stone": "石",
    "Iron": "铁",
    "Golden": "金",
    "Diamond": "钻石",
    "Leather": "皮革",
    "Chainmail": "锁链",
}


class Glossary:
    """Translation glossary with optional custom overrides.

    Usage::

        glossary = Glossary()
        glossary.load_custom("my_terms.csv")
        print(glossary.get("Iron Sword"))  # → None if not found
    """

    def __init__(self) -> None:
        self._entries: dict[str, str] = dict(BUILTIN_GLOSSARY)

    def load_custom(self, path: Path) -> int:
        """Load a custom glossary from a CSV or JSON file.

        CSV format: ``English,Chinese`` (one per line, first line is header).
        JSON format: ``{"English Term": "中文术语", ...}``.

        Custom entries override built-in entries with the same key.

        Args:
            path: Path to CSV or JSON file.

        Returns:
            Number of entries loaded.
        """
        suffix = path.suffix.lower()
        new_entries: dict[str, str] = {}

        if suffix == ".json":
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                new_entries = {str(k): str(v) for k, v in data.items()}
        elif suffix in (".csv", ".tsv"):
            delimiter = "\t" if suffix == ".tsv" else ","
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                for row in reader:
                    if len(row) >= 2:
                        key, value = row[0].strip(), row[1].strip()
                        if key and not key.lower().startswith("english"):
                            new_entries[key] = value
        else:
            raise ValueError(
                f"Unsupported glossary format: {suffix}. Use .json, .csv, or .tsv"
            )

        self._entries.update(new_entries)
        logger.info("Loaded %d glossary entries from %s", len(new_entries), path)
        return len(new_entries)

    def get(self, english: str) -> Optional[str]:
        """Look up a known translation for an English term.

        Args:
            english: The English text to look up.

        Returns:
            Chinese translation if found, None otherwise.
        """
        return self._entries.get(english)

    def to_dict(self) -> dict[str, str]:
        """Return a copy of all glossary entries."""
        return dict(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries
