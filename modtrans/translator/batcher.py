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

    Algorithm:
    1. Filter mods that need translation
    2. Sort by untranslated key count (descending)
    3. Large mods (> max_batch_keys) → split into ceil(keys/max) sub-batches
    4. Small mods → greedily combine into bundles ≤ max_batch_keys

    Args:
        max_batch_keys: Maximum keys per API call (default 1000).
    """

    def __init__(self, max_batch_keys: int = 1000) -> None:
        self.max_batch_keys = max_batch_keys

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def group(self, mods: list[ModAssets]) -> list[TranslationBatch]:
        """Group mods into translation batches.

        Returns:
            List of TranslationBatch objects, each ≤ max_batch_keys keys.
        """
        # Filter to mods that actually need translation
        pending: list[ModAssets] = []
        for mod in mods:
            if self._untranslated_keys(mod):
                pending.append(mod)

        if not pending:
            logger.info("没有需要翻译的条目 — 所有文本已有汉化")
            return []

        # Sort by untranslated key count descending
        pending.sort(key=lambda m: len(self._untranslated_keys(m)), reverse=True)

        batches: list[TranslationBatch] = []
        bundle_mods: list[ModAssets] = []
        bundle_keys = 0

        for mod in pending:
            key_count = len(self._untranslated_keys(mod))

            # ---- large mod: split into sub-batches ----
            if key_count > self.max_batch_keys:
                # flush current bundle first
                if bundle_mods:
                    batches.append(self._create_batch(
                        bundle_mods, self._bundle_id(bundle_mods),
                    ))
                    bundle_mods = []
                    bundle_keys = 0

                untranslated = sorted(self._untranslated_keys(mod))
                total = len(untranslated)
                chunks = (total + self.max_batch_keys - 1) // self.max_batch_keys
                mod_name = mod.metadata.name or mod.modid
                logger.info(
                    "%s 有 %d 条待翻译，拆为 %d 批（每批 ≤ %d）",
                    mod_name, total, chunks, self.max_batch_keys,
                )
                for i in range(0, total, self.max_batch_keys):
                    chunk = set(untranslated[i : i + self.max_batch_keys])
                    chunk_num = i // self.max_batch_keys + 1
                    batches.append(self._create_batch(
                        [mod],
                        f"{mod.modid}/{chunk_num}",
                        key_filter=chunk,
                    ))
                continue

            # ---- small mod: try to add to bundle ----
            if bundle_keys + key_count > self.max_batch_keys and bundle_mods:
                # would exceed — flush current bundle
                batches.append(self._create_batch(
                    bundle_mods, self._bundle_id(bundle_mods),
                ))
                bundle_mods = []
                bundle_keys = 0

            bundle_mods.append(mod)
            bundle_keys += key_count

        # flush final bundle
        if bundle_mods:
            batches.append(self._create_batch(
                bundle_mods, self._bundle_id(bundle_mods),
            ))

        # summary
        solo_count = sum(1 for b in batches if len(b.mods) == 1)
        bundle_count = sum(1 for b in batches if len(b.mods) > 1)
        logger.info(
            "共 %d 个批次（%d 个独立, %d 个合并）",
            len(batches), solo_count, bundle_count,
        )
        return batches

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bundle_id(mods: list[ModAssets]) -> str:
        """Generate a batch ID from a bundle of mods."""
        if len(mods) == 1:
            return mods[0].modid
        return "+".join(m.modid for m in mods[:4]) + (
            f"+{len(mods) - 4}" if len(mods) > 4 else ""
        )

    def _create_batch(
        self,
        mods: list[ModAssets],
        batch_id: str,
        key_filter: set[str] | None = None,
    ) -> TranslationBatch:
        """Create a TranslationBatch from a list of mods.

        Args:
            mods: Mods to include in this batch.
            batch_id: Human-readable batch identifier.
            key_filter: Optional key subset (used when splitting a large mod).
        """
        all_entries: dict[str, str] = {}
        all_existing_ref: dict[str, str] = {}

        for mod in mods:
            untranslated = self._untranslated_keys(mod)
            if key_filter is not None:
                untranslated = untranslated & key_filter
            for key in untranslated:
                all_entries[key] = mod.english_entries[key]

            # existing Chinese as reference
            for key, zh_value in mod.existing_chinese.items():
                en_value = mod.english_entries.get(key)
                if en_value is None or zh_value.strip() != en_value.strip():
                    all_existing_ref[key] = zh_value

        # context string
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

        context = "; ".join(mod_names)

        return TranslationBatch(
            batch_id=batch_id,
            mods=mods,
            total_keys=len(all_entries),
            entries=all_entries,
            existing_reference=all_existing_ref,
            context_info=context,
        )

    @staticmethod
    def _untranslated_keys(mod: ModAssets) -> set[str]:
        """Return English keys that still need translation.

        Keys already having a valid (non-English) zh_cn are excluded.
        Keys matching known compatibility bug patterns are also excluded.
        """
        from ..compat import filter_compat_keys

        all_keys = set(mod.english_entries.keys())
        already_translated: set[str] = set()

        for key, zh_value in mod.existing_chinese.items():
            en_value = mod.english_entries.get(key)
            if en_value is not None and zh_value.strip() != en_value.strip():
                already_translated.add(key)

        untranslated = all_keys - already_translated

        # 兼容性过滤：排除已知会触发 mod bug 的键
        untranslated, _kept_english = filter_compat_keys(mod.modid, untranslated)

        return untranslated
