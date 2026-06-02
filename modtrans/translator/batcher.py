"""Translation batch grouping.

Sorts mods by untranslated key count (descending). Large mods are split
into sub-batches; small mods are greedily combined into bundles without
exceeding max_batch_keys. No mod is ever split across bundles.
"""

from __future__ import annotations

import logging

from ..models import ModAssets, TranslationBatch

logger = logging.getLogger(__name__)


class Batcher:
    """Groups ModAssets into TranslationBatches for API calls.

    Args:
        max_batch_keys: Maximum keys per API call (default 1000).
    """

    def __init__(self, max_batch_keys: int = 1000) -> None:
        self.max_batch_keys = max_batch_keys
        self._cache: dict[int, set[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def group(
        self,
        mods: list[ModAssets],
        key_filter: set[str] | None = None,
    ) -> list[TranslationBatch]:
        """Group mods into translation batches.

        Args:
            mods: List of mods to batch.
            key_filter: Optional key subset to restrict batching to.
        """
        self._cache.clear()
        for mod in mods:
            self._cache_keys(mod)

        pending: list[tuple[ModAssets, set[str]]] = []
        for mod in mods:
            keys = self._get_cached_keys(mod)
            if key_filter is not None:
                keys = keys & key_filter
            if keys:
                pending.append((mod, keys))

        if not pending:
            if key_filter is not None:
                logger.info("没有需要翻译的条目（在指定 key 范围内）")
            else:
                logger.info("没有需要翻译的条目 — 所有文本已有汉化")
            self._cache.clear()
            return []

        pending.sort(key=lambda x: len(x[1]), reverse=True)

        batches: list[TranslationBatch] = []
        bundle_mods: list[ModAssets] = []
        bundle_keys_count = 0

        for mod, mod_keys in pending:
            key_count = len(mod_keys)

            if key_count > self.max_batch_keys:
                if bundle_mods:
                    batches.append(self._create_batch(bundle_mods, self._bundle_id(bundle_mods)))
                    bundle_mods = []
                    bundle_keys_count = 0

                sorted_keys = sorted(mod_keys)
                total = len(sorted_keys)
                chunks = (total + self.max_batch_keys - 1) // self.max_batch_keys
                mod_name = mod.metadata.name or mod.modid
                logger.info("%s 有 %d 条待翻译，拆为 %d 批（每批 ≤ %d）", mod_name, total, chunks, self.max_batch_keys)
                for i in range(0, total, self.max_batch_keys):
                    chunk = set(sorted_keys[i : i + self.max_batch_keys])
                    chunk_num = i // self.max_batch_keys + 1
                    batches.append(self._create_batch([mod], f"{mod.modid}/{chunk_num}", key_filter=chunk))
                continue

            if bundle_keys_count + key_count > self.max_batch_keys and bundle_mods:
                batches.append(self._create_batch(bundle_mods, self._bundle_id(bundle_mods)))
                bundle_mods = []
                bundle_keys_count = 0

            bundle_mods.append(mod)
            bundle_keys_count += key_count

        if bundle_mods:
            batches.append(self._create_batch(bundle_mods, self._bundle_id(bundle_mods)))

        solo_count = sum(1 for b in batches if len(b.mods) == 1)
        bundle_count = sum(1 for b in batches if len(b.mods) > 1)
        logger.info("共 %d 个批次（%d 个独立, %d 个合并）", len(batches), solo_count, bundle_count)

        self._cache.clear()
        return batches

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cache_keys(self, mod: ModAssets) -> None:
        key = id(mod)
        if key not in self._cache:
            self._cache[key] = self._untranslated_keys(mod)

    def _get_cached_keys(self, mod: ModAssets) -> set[str]:
        key = id(mod)
        if key not in self._cache:
            self._cache_keys(mod)
        return self._cache[key]

    @staticmethod
    def _bundle_id(mods: list[ModAssets]) -> str:
        if len(mods) == 1:
            return mods[0].modid
        return "+".join(m.modid for m in mods[:4]) + (f"+{len(mods) - 4}" if len(mods) > 4 else "")

    def _create_batch(
        self,
        mods: list[ModAssets],
        batch_id: str,
        key_filter: set[str] | None = None,
    ) -> TranslationBatch:
        all_entries: dict[str, str] = {}
        for mod in mods:
            untranslated = self._get_cached_keys(mod)
            if key_filter is not None:
                untranslated = untranslated & key_filter
            for key in untranslated:
                all_entries[key] = mod.english_entries[key]

        mod_names = []
        for mod in mods:
            name = mod.metadata.name or mod.modid
            author = mod.metadata.author
            version = mod.metadata.game_version or mod.metadata.version
            if author:
                name = f"{name} by {author}"
            if version:
                name = f"{name} (MC {version})"
            mod_names.append(name)

        return TranslationBatch(
            batch_id=batch_id,
            mods=mods,
            total_keys=len(all_entries),
            entries=all_entries,
            context_info="; ".join(mod_names),
        )

    @staticmethod
    def _untranslated_keys(mod: ModAssets) -> set[str]:
        from ..compat import filter_compat_keys
        all_keys = set(mod.english_entries.keys())
        already_translated: set[str] = set()
        for key, zh_value in mod.existing_chinese.items():
            en_value = mod.english_entries.get(key)
            if en_value is not None and zh_value.strip() != en_value.strip():
                already_translated.add(key)
        untranslated = all_keys - already_translated
        untranslated, _kept_english = filter_compat_keys(mod.modid, untranslated)
        return untranslated
