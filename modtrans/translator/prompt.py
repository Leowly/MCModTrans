"""AI prompt management for Minecraft mod translation.

The system prompt is a MODULE-LEVEL FROZEN CONSTANT. It is never modified
or constructed dynamically at runtime. This ensures byte-for-byte identical
system messages across all API calls, maximizing prompt cache hit rate on
OpenAI-compatible APIs.

Design:
- SYSTEM_PROMPT: complete system prompt with all instructions and references
- build_user_message(): constructs the dynamic user message per batch
- classify_existing_english(): identifies zh_cn entries that still contain English
"""

from __future__ import annotations

from typing import Optional

# ===========================================================================
# FROZEN SYSTEM PROMPT — DO NOT MODIFY AT RUNTIME
# ===========================================================================
# This string is shared by every single API call. Keeping it constant
# maximizes the prompt cache hit rate on OpenAI-compatible APIs.
# If you need to customize it, use the PromptConfig.custom_prefix setting,
# but be aware that changes invalidate the cache for the first call.

SYSTEM_PROMPT: str = """\
You are a Minecraft mod localization expert. Your task is to translate \
English Minecraft mod display text into Simplified Chinese (zh_CN).

Follow these rules strictly:

## 1. TRANSLATION QUALITY
- Produce natural, fluent Chinese suitable for a game UI.
- For items and blocks: prefer standard Minecraft Chinese translation \
conventions (e.g., "Iron Sword" → "铁剑", "Dirt" → "泥土").
- For technical terms: use commonly accepted translations from the \
Minecraft Chinese community.
- Maintain the original tone — informal, formal, whimsical, or mechanical \
as appropriate.

## 2. FORMATTING PRESERVATION (CRITICAL)
DO NOT modify, translate, or alter these formatting codes in ANY way:
- Minecraft color/format codes: §[0-9a-fk-or] (keep EXACTLY as-is)
- Java printf placeholders: %s, %d, %f, %n$s, %1$s, %2$d, %%.0f, etc.
- Braced placeholders: {0}, {1}, {2} (keep EXACTLY)
- Escape sequences: \\n, \\t, \\", \\\\ (keep EXACTLY)
- Keep ALL formatting characters in their EXACT original positions.

## 3. DO NOT TRANSLATE
- Mod IDs, registry keys, internal identifiers, command syntax
- Proper names (mod names, author names, brand names)
- URLs, IP addresses, file paths
- Numbers, coordinates, version strings
- Minecraft color codes and formatting codes

## 4. EXISTING ZH_CN WITH ENGLISH TEXT
- Some entries may already have a zh_cn key but the value is still English.
- If the English text is a PROPER NOUN (brand name, person name, unique \
identifier) that should remain in English even in a Chinese context, \
keep it as-is.
- If the English text is a regular phrase/description that was simply \
not translated yet, translate it normally.
- Use your judgment: "Iron Sword" should be "铁剑", but "OpenComputers" \
is a mod name and should stay as "OpenComputers".

## 5. REFERENCE TRANSLATIONS
Common Minecraft terminology for consistency:
  Iron Sword → 铁剑              Diamond Pickaxe → 钻石镐
  Dirt → 泥土                    Stone → 石头
  Cobblestone → 圆石             Gravel → 沙砾
  Sand → 沙子                    Glass → 玻璃
  Crafting Table → 工作台         Furnace → 熔炉
  Chest → 箱子                   Crafting → 合成
  Grass Block → 草方块            Oak Wood → 橡木
  Redstone → 红石                Nether → 下界
  The End → 末地                 Overworld → 主世界
  Creeper → 苦力怕               Enderman → 末影人
  Zombie → 僵尸                  Skeleton → 骷髅
  Spider → 蜘蛛                  Slime → 史莱姆
  HP / Health → 生命值           Damage → 伤害
  Armor → 盔甲                   Durability → 耐久度
  Enchantment → 附魔             Level → 等级
  Experience → 经验值            Inventory → 物品栏
  Sword → 剑                     Pickaxe → 镐
  Axe → 斧                       Shovel → 锹
  Hoe → 锄                       Bow → 弓
  Arrow → 箭                     Shield → 盾牌
  Helmet → 头盔                  Chestplate → 胸甲
  Leggings → 护腿                Boots → 靴子
  Food → 食物                    Hunger → 饥饿值
  Saturation → 饱和度            Potion → 药水
  Biome → 生物群系               Dimension → 维度
  Block → 方块                   Item → 物品
  Entity → 实体                  Mob → 生物
  Spawn → 生成                   Despawn → 消失
  Ticks → 刻                     Seconds → 秒
  Minutes → 分钟                 Configuration → 配置
  Enabled → 启用                 Disabled → 禁用
  On → 开                        Off → 关
  Yes → 是                       No → 否
  True → 是                      False → 否

## 6. OUTPUT FORMAT
- Return ONLY a valid JSON object.
- Keys MUST be exactly as provided (identical strings).
- Values MUST be the Chinese translation.
- NO markdown code blocks (no ```json```).
- NO explanations, NO preamble, NO postamble.
- The ENTIRE response must be parseable by json.loads().
- Example correct output: {"key1": "翻译1", "key2": "翻译2"}
"""

# ===========================================================================
# User message builder
# ===========================================================================


def build_user_message(
    entries: dict[str, str],
    mod_context: str = "",
    existing_chinese: Optional[dict[str, str]] = None,
    keep_english_keys: Optional[set[str]] = None,
) -> str:
    """Build the dynamic user message containing the entries to translate.

    Args:
        entries: Translation key → English text to translate.
        mod_context: Human-readable context like "Tinkers' Construct by mDiyo (MC 1.12.2)".
        existing_chinese: Pre-existing zh_cn entries already in Chinese
            (shown as reference, not to be translated).
        keep_english_keys: Keys where the existing zh_cn value is English
            text that may be a proper noun — AI decides whether to keep
            or translate.

    Returns:
        Formatted user message string.
    """
    parts: list[str] = []

    # Context header
    if mod_context:
        parts.append(f"Mod: {mod_context}")

    # Existing Chinese reference translations (correct translations, skip)
    if existing_chinese:
        filtered = _filter_valid_translations(existing_chinese, entries)
        if filtered:
            parts.append(
                "Reference (ALREADY translated correctly — do NOT translate again):"
            )
            parts.append(_format_json_block(filtered))

    # Keys where zh_cn has English text — AI should decide
    if keep_english_keys:
        keys_to_check = {
            k: entries[k] for k in keep_english_keys if k in entries
        }
        if keys_to_check:
            parts.append(
                "These keys already have a zh_cn entry but it contains ENGLISH text. "
                "Decide for each: if it's a proper noun (mod name, brand, person) "
                "that should stay English, keep it as-is. Otherwise, translate it:"
            )
            parts.append(_format_json_block(keys_to_check))

    # Entries to translate (exclude already-translated and already-handled)
    skip_keys: set[str] = set()
    if existing_chinese:
        skip_keys.update(_filter_valid_translations(existing_chinese, entries).keys())
    if keep_english_keys:
        skip_keys.update(keep_english_keys)

    to_translate = {k: v for k, v in entries.items() if k not in skip_keys}

    if not to_translate:
        parts.append("Translate the following to zh_CN (may be empty):")
        parts.append("{}")
    else:
        parts.append(
            f"Translate the following {len(to_translate)} entries to zh_CN. "
            "Output ONLY the JSON object:"
        )
        parts.append(_format_json_block(to_translate))

    return "\n\n".join(parts)


def classify_existing_chinese(
    english_entries: dict[str, str],
    existing_chinese: dict[str, str],
) -> tuple[dict[str, str], set[str]]:
    """Analyze pre-existing zh_cn entries to determine what to skip vs. review.

    Args:
        english_entries: The source en_us entries.
        existing_chinese: The pre-existing zh_cn entries.

    Returns:
        Tuple of (fully_translated dict, keys_to_review set).
        - fully_translated: entries where zh_cn value differs from en_us
          (confidently translated — skip these)
        - keys_to_review: keys where zh_cn value equals en_us value
          (possibly a proper noun that should stay English — let AI decide)
    """
    fully_translated: dict[str, str] = {}
    keys_to_review: set[str] = set()

    for key, zh_value in existing_chinese.items():
        en_value = english_entries.get(key)
        if en_value is None:
            # Key exists in zh_cn but not in en_us — keep as reference
            fully_translated[key] = zh_value
        elif zh_value.strip() == en_value.strip():
            # zh_cn has the same English text — needs review
            keys_to_review.add(key)
        else:
            # zh_cn differs from en_us — properly translated
            fully_translated[key] = zh_value

    return fully_translated, keys_to_review


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_valid_translations(
    existing_chinese: dict[str, str],
    english_entries: dict[str, str],
) -> dict[str, str]:
    """Return only zh_cn entries that differ from their en_us counterparts."""
    result: dict[str, str] = {}
    for key, zh_value in existing_chinese.items():
        en_value = english_entries.get(key)
        if en_value is None or zh_value.strip() != en_value.strip():
            result[key] = zh_value
    return result


def _format_json_block(data: dict[str, str]) -> str:
    """Format a dictionary as a compact JSON string for the prompt."""
    import json
    return json.dumps(data, ensure_ascii=False, indent=2)
