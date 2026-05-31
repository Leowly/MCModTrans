"""Translation batch grouping strategies.

Groups ModAssets into optimally-sized TranslationBatches for API calls.
Strategies:
- author: Group by mod author (translation consistency within author's work)
- size:   Large mods solo, small mods bundled together
- modid:  One batch per mod
- none:   All mods in one batch (auto-split if too large)
"""

from __future__ import annotations

import logging
from enum import Enum

from ..models import ModAssets, TranslationBatch

logger = logging.getLogger(__name__)


class BatchingStrategy(Enum):
    AUTHOR = "author"
    SIZE = "size"
    MODID = "modid"
    NONE = "none"


class Batcher:
    """Groups ModAssets into TranslationBatches for API calls.

    Args:
        strategy: How to group mods together.
        max_batch_keys: Maximum total translation keys per batch.
        min_keys_for_solo: Mods with fewer keys than this get bundled.
    """

    def __init__(
        self,
        strategy: BatchingStrategy = BatchingStrategy.AUTHOR,
        max_batch_keys: int = 500,
        min_keys_for_solo: int = 50,
    ) -> None:
        self.strategy = strategy
        self.max_batch_keys = max_batch_keys
        self.min_keys_for_solo = min_keys_for_solo

    def group(self, mods: list[ModAssets]) -> list[TranslationBatch]:
        """Group mods into translation batches.

        Only mods with untranslated keys are included. Mods where all
        English entries already have valid zh_cn translations are skipped.

        Returns:
            List of TranslationBatch objects, each ready for one API call.
        """
        # Filter to mods that actually need translation
        pending: list[ModAssets] = []
        for mod in mods:
            untranslated = self._untranslated_keys(mod)
            if untranslated:
                pending.append(mod)

        if not pending:
            logger.info("No mods need translation — all entries already have zh_cn")
            return []

        # Dispatch to strategy
        if self.strategy == BatchingStrategy.AUTHOR:
            return self._group_by_author(pending)
        elif self.strategy == BatchingStrategy.SIZE:
            return self._group_by_size(pending)
        elif self.strategy == BatchingStrategy.MODID:
            return self._group_by_modid(pending)
        else:  # NONE
            return self._group_none(pending)

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _group_by_author(self, mods: list[ModAssets]) -> list[TranslationBatch]:
        """Group by metadata.author. Mods from the same author
        are batched together. Unknown-author mods are grouped by modid.
        Large author groups are split if they exceed max_batch_keys."""
        author_groups: dict[str, list[ModAssets]] = {}

        for mod in mods:
            author = mod.metadata.author.strip() if mod.metadata.author else ""
            if not author:
                author = f"_modid_{mod.modid}"
            author_groups.setdefault(author, []).append(mod)

        return self._build_batches(author_groups)

    def _group_by_size(self, mods: list[ModAssets]) -> list[TranslationBatch]:
        """Sort by key count. Large mods get solo batches; small mods
        are bundled together up to max_batch_keys."""
        sorted_mods = sorted(
            mods,
            key=lambda m: len(self._untranslated_keys(m)),
            reverse=True,
        )

        batches: list[TranslationBatch] = []
        bundle: list[ModAssets] = []
        bundle_keys = 0

        for mod in sorted_mods:
            key_count = len(self._untranslated_keys(mod))

            if key_count >= self.min_keys_for_solo:
                # Large mod — solo batch (may still need splitting)
                if bundle:
                    batches.extend(self._build_batches({"_bundled": bundle}))
                    bundle = []
                    bundle_keys = 0

                solo_batches = self._split_if_needed(
                    [mod], f"_solo_{mod.modid}"
                )
                batches.extend(solo_batches)
            else:
                # Small mod — bundle it
                if (
                    bundle_keys + key_count > self.max_batch_keys
                    and bundle
                ):
                    batches.extend(
                        self._build_batches(
                            {"_bundled": bundle},
                            counter=getattr(self, "_bundle_counter", 0),
                        )
                    )
                    setattr(
                        self,
                        "_bundle_counter",
                        getattr(self, "_bundle_counter", 0) + 1,
                    )
                    bundle = []
                    bundle_keys = 0

                bundle.append(mod)
                bundle_keys += key_count

        # Don't forget the last bundle
        if bundle:
            batches.extend(
                self._build_batches(
                    {"_bundled": bundle},
                    counter=getattr(self, "_bundle_counter", 0),
                )
            )

        return batches

    def _group_by_modid(self, mods: list[ModAssets]) -> list[TranslationBatch]:
        """Each mod gets its own batch (may still be subject to max_batch_keys)."""
        result: list[TranslationBatch] = []
        for mod in mods:
            result.extend(
                self._split_if_needed([mod], f"_modid_{mod.modid}")
            )
        return result

    def _group_none(self, mods: list[ModAssets]) -> list[TranslationBatch]:
        """All mods in one batch. Auto-split if exceeding max_batch_keys."""
        return self._split_if_needed(mods, "_all")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_batches(
        self,
        groups: dict[str, list[ModAssets]],
        counter: int = 0,
    ) -> list[TranslationBatch]:
        """Build TranslationBatch objects from pre-grouped mods."""
        batches: list[TranslationBatch] = []
        for group_key, mods in groups.items():
            batch = self._create_batch(mods, group_key)
            if batch.total_keys > self.max_batch_keys:
                # Split large groups
                batches.extend(self._split_if_needed(mods, group_key))
            else:
                batches.append(batch)
        return batches

    def _split_if_needed(
        self, mods: list[ModAssets], base_id: str
    ) -> list[TranslationBatch]:
        """Split a list of mods into sub-batches respecting max_batch_keys."""
        batches: list[TranslationBatch] = []
        current_mods: list[ModAssets] = []
        current_keys = 0
        split_idx = 0

        for mod in mods:
            key_count = len(self._untranslated_keys(mod))

            if (
                current_keys + key_count > self.max_batch_keys
                and current_mods
            ):
                batch_id = f"{base_id}_{split_idx}" if split_idx > 0 else base_id
                batches.append(self._create_batch(current_mods, batch_id))
                current_mods = []
                current_keys = 0
                split_idx += 1

            current_mods.append(mod)
            current_keys += key_count

        if current_mods:
            batch_id = f"{base_id}_{split_idx}" if split_idx > 0 else base_id
            batches.append(self._create_batch(current_mods, batch_id))

        return batches

    def _create_batch(
        self, mods: list[ModAssets], batch_id: str
    ) -> TranslationBatch:
        """Create a TranslationBatch from a list of mods."""
        # Collect all English entries that need translation
        all_entries: dict[str, str] = {}
        all_existing_ref: dict[str, str] = {}

        for mod in mods:
            untranslated = self._untranslated_keys(mod)
            for key in untranslated:
                all_entries[key] = mod.english_entries[key]

            # Collect existing Chinese as reference
            for key, zh_value in mod.existing_chinese.items():
                en_value = mod.english_entries.get(key)
                if en_value is None or zh_value.strip() != en_value.strip():
                    all_existing_ref[key] = zh_value

        # Build context string
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
            existing_reference=all_existing_ref,
            context_info=context,
        )

    @staticmethod
    def _untranslated_keys(mod: ModAssets) -> set[str]:
        """Return the set of English keys that need translation.

        Keys that already have a valid (non-English) zh_cn translation
        are excluded.
        """
        all_keys = set(mod.english_entries.keys())
        already_translated: set[str] = set()

        for key, zh_value in mod.existing_chinese.items():
            en_value = mod.english_entries.get(key)
            if en_value is not None and zh_value.strip() != en_value.strip():
                # Already has a proper Chinese translation
                already_translated.add(key)

        return all_keys - already_translated
